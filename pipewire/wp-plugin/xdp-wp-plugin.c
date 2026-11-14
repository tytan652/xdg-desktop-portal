/* SPDX-License-Identifier: LGPL-2.1-or-later */
/* SPDX-FileCopyrightText: Copyright © the xdg-desktop-portal contributors */

#include <pipewire/keys.h>
#include <wp/wp.h>

#include "xdp-wp-client-manager.h"
#include "xdp-wp-metadata.h"

WP_DEFINE_LOCAL_LOG_TOPIC ("m-xdp-plugin");

G_DECLARE_FINAL_TYPE (XdpWpPlugin, xdp_wp_plugin, XDP_WP, PLUGIN, WpPlugin);

struct _XdpWpPlugin
{
  WpPlugin parent_instance;

  WpSpaJson *args;
  WpObjectInterest *camera_interest;

  GCancellable *cancellable;

  XdpWpMetadata *metadata;
  WpObjectManager *camera_om;
  XdpWpClientManager *client_manager;
};

G_DEFINE_FINAL_TYPE (XdpWpPlugin, xdp_wp_plugin, WP_TYPE_PLUGIN);

typedef enum
{
  PROP_ARGS = 1,
} XdpWpPluginProps;

static GParamSpec *props[PROP_ARGS + 1] = { NULL, };

static void
metadata_init_cb (GObject      *object,
                  GAsyncResult *res,
                  gpointer      user_data)
{
  XdpWpPlugin *self = XDP_WP_PLUGIN (user_data);
  g_autoptr(WpCore) core = wp_object_get_core (WP_OBJECT (self));;
  g_autoptr(GError) error = NULL;

  self->metadata = xdp_wp_metadata_new_finish (object, res, &error);
  if (G_UNLIKELY (self->metadata == NULL))
    {
      wp_critical_object (self, "Error while creating metadata: %s", error->message);
      return;
    }

  wp_core_install_object_manager (core, self->camera_om);

  self->client_manager = xdp_wp_client_manager_new (core, self->camera_om);
}

static void
xdp_wp_plugin_enable (WpPlugin     *plugin,
                      WpTransition *transition)
{
  XdpWpPlugin *self = XDP_WP_PLUGIN (plugin);
  g_autoptr(WpCore) core = wp_object_get_core (WP_OBJECT (self));
  g_autoptr(GError) error = NULL;

  if (!wp_object_interest_validate (self->camera_interest, &error))
    {
      g_prefix_error_literal (&error, "Invalid camera interest: ");
      wp_transition_return_error (transition, g_steal_pointer (&error));
      return;
    }

  self->cancellable = g_cancellable_new ();

  self->camera_om = wp_object_manager_new ();
  wp_object_manager_add_interest_full (self->camera_om,
                                       wp_object_interest_ref (self->camera_interest));

  xdp_wp_metadata_new (core,
                       self->camera_om,
                       self->cancellable,
                       metadata_init_cb,
                       self);

  wp_object_update_features (WP_OBJECT (self), WP_PLUGIN_FEATURE_ENABLED, 0);
}

static void
xdp_wp_plugin_disable (WpPlugin *plugin)
{
  XdpWpPlugin *self = XDP_WP_PLUGIN (plugin);

  g_cancellable_cancel (self->cancellable);

  g_clear_object (&self->client_manager);
  g_clear_object (&self->metadata);

  g_clear_object (&self->camera_om);

  g_clear_object (&self->cancellable);

  wp_object_update_features (WP_OBJECT (self), 0, WP_PLUGIN_FEATURE_ENABLED);
}

static void
xdp_wp_plugin_set_property (GObject      *object,
                            guint         property_id,
                            const GValue *value,
                            GParamSpec   *pspec)
{
  XdpWpPlugin *self = XDP_WP_PLUGIN (object);

  switch ((XdpWpPluginProps) property_id)
    {
    case PROP_ARGS:
      self->args = g_value_dup_boxed (value);
      break;
    }
}

static void
xdp_wp_plugin_constructed (GObject *object)
{
  XdpWpPlugin *self = XDP_WP_PLUGIN (object);
  g_autoptr(WpSpaJson) args = g_steal_pointer (&self->args);
  g_autoptr(WpSpaJson) camera_device_apis = NULL;
  GVariant *variant = NULL;
  g_autofree char *variant_string = NULL;

  G_OBJECT_CLASS (xdp_wp_plugin_parent_class)->constructed (object);

  if (args != NULL)
    wp_spa_json_object_get (args,
                            "camera.device-apis", "J", &camera_device_apis,
                            NULL);

  self->camera_interest = wp_object_interest_new_type (WP_TYPE_NODE);
  if (camera_device_apis && wp_spa_json_is_array (camera_device_apis))
    {
      g_autoptr(GVariantBuilder) builder = NULL;
      g_autoptr(WpSpaJsonParser) parser = wp_spa_json_parser_new_array (camera_device_apis);
      g_autofree char *api = NULL;

      wp_debug_object (self, "Array camera.device-apis found in arguments");
      for (; (api = wp_spa_json_parser_get_string (parser)); g_free (api))
        {
          if (builder == NULL)
            builder = g_variant_builder_new (G_VARIANT_TYPE_TUPLE);

          wp_debug_object (self, "%s found in camera.device-apis", api);
          g_variant_builder_add_value (builder, g_variant_new_string (api));
        }

      if (builder != NULL)
        variant = g_variant_builder_end (builder);
    }

  if (variant == NULL)
    {
      variant = g_variant_new ("(ss)", "v4l2", "libcamera");
      wp_warning_object (self, "Camera device APIs fallback set");
    }

  variant_string = g_variant_print (variant, TRUE);
  wp_info_object (self, "Camera device APIs: %s", variant_string);

  wp_object_interest_add_constraint (self->camera_interest,
                                     WP_CONSTRAINT_TYPE_PW_PROPERTY,
                                     PW_KEY_DEVICE_API,
                                     WP_CONSTRAINT_VERB_IN_LIST,
                                     g_steal_pointer (&variant));
  wp_object_interest_add_constraint (self->camera_interest,
                                     WP_CONSTRAINT_TYPE_PW_PROPERTY,
                                     PW_KEY_MEDIA_CLASS,
                                     WP_CONSTRAINT_VERB_EQUALS,
                                     g_variant_new_string ("Video/Source"));
}

static void
xdp_wp_plugin_finalize (GObject *object)
{
  XdpWpPlugin *self = XDP_WP_PLUGIN (object);

  g_clear_pointer (&self->camera_interest, wp_object_interest_unref);

  G_OBJECT_CLASS (xdp_wp_plugin_parent_class)->finalize (object);
}

static void
xdp_wp_plugin_class_init (XdpWpPluginClass *klass)
{
  GObjectClass *object_class = G_OBJECT_CLASS (klass);
  WpPluginClass *plugin_class = WP_PLUGIN_CLASS (klass);

  object_class->set_property = xdp_wp_plugin_set_property;
  object_class->constructed = xdp_wp_plugin_constructed;
  object_class->finalize = xdp_wp_plugin_finalize;

  plugin_class->enable = xdp_wp_plugin_enable;
  plugin_class->disable = xdp_wp_plugin_disable;

  props[PROP_ARGS] = g_param_spec_boxed ("args", NULL, NULL,
                                         WP_TYPE_SPA_JSON,
                                         G_PARAM_WRITABLE | G_PARAM_CONSTRUCT_ONLY | G_PARAM_STATIC_STRINGS);

  g_object_class_install_properties (object_class, G_N_ELEMENTS (props), props);
}

static void
xdp_wp_plugin_init (XdpWpPlugin *self)
{
}

WP_PLUGIN_EXPORT GObject *
wireplumber__module_init (WpCore     *core,
                          WpSpaJson  *args,
                          GError    **error)
{
  return g_object_new (xdp_wp_plugin_get_type (),
                       "name", "xdp-desktop-portal",
                       "core", core,
                       "args", args,
                       NULL);
}
