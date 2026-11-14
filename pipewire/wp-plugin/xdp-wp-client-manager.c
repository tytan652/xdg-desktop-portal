/* SPDX-License-Identifier: LGPL-2.1-or-later */
/* SPDX-FileCopyrightText: Copyright © the xdg-desktop-portal contributors */

#include "xdp-wp-client-manager.h"

#include <gio/gio.h>
#include <glib-object.h>
#include <pipewire/core.h>
#include <pipewire/keys.h>
#include <pipewire/permission.h>
#include <wp/wp.h>

#include "xdp-impl-dbus.h"
#include "xdp-pw-keys.h"

WP_DEFINE_LOCAL_LOG_TOPIC ("m-xdp-client-manager");

/* Copied from dbus-connection-state.h */
typedef enum {
  WP_DBUS_CONNECTION_STATE_CLOSED = 0,
  WP_DBUS_CONNECTION_STATE_CONNECTING,
  WP_DBUS_CONNECTION_STATE_CONNECTED,
} WpDBusConnectionState;

struct _XdpWpClientManager
{
  GObject parent_instance;

  WpCore *core;

  WpPlugin *dbus_connection_plugin;
  gulong dbus_changed_signal_id;

  GCancellable *cancellable;

  WpObjectInterest *camera_scope;

  XdpDbusImplPermissionStore *permission_store;
  gulong permission_changed_signal_id;

  guint node_factory_id;

  WpObjectManager *camera_om;
  gulong camera_added_signal_id;

  WpObjectManager *client_om;
  gulong client_added_signal_id;
};

G_DEFINE_FINAL_TYPE (XdpWpClientManager, xdp_wp_client_manager, G_TYPE_OBJECT);

typedef enum
{
  PROP_CORE = 1,
  PROP_CAMERA_OM,
} XdpWpClientManagerProps;

static GParamSpec *props[PROP_CAMERA_OM + 1] = { NULL, };

static void
update_client_camera_permission (XdpWpClientManager *self,
                                 WpClient           *client,
                                 GVariantIter       *iter)
{
  g_autofree char *value = NULL;
  gboolean allowed;
  const char *scope = NULL;
  g_auto(GStrv) scopev = NULL;
  g_autoptr(WpIterator) node_iter = NULL;
  g_auto(GValue) node_value = G_VALUE_INIT;

  allowed =
    iter && g_variant_iter_next (iter, "s", &value) && (g_strcmp0 (value, "yes") == 0);

  scope = wp_pipewire_object_get_property (WP_PIPEWIRE_OBJECT (client),
                                           XDP_PW_KEY_SCOPE);

  if (!scope)
    {
      wp_warning_object (client, "Client has no portal scope set");
      return;
    }

  scopev = g_strsplit (scope, ",", 0);

  if (!g_strv_contains ((const gchar* const*) scopev, "Camera"))
    return;

  wp_info_object (client, "Update camera permissions");

  wp_client_update_permissions (client, 1, wp_proxy_get_bound_id (WP_PROXY (client)),
                                allowed ? PW_PERM_ALL : 0);

  node_iter = wp_object_manager_new_iterator (self->camera_om);
  while (wp_iterator_next (node_iter, &node_value))
    {
      WpNode *node = g_value_get_object (&node_value);

      wp_client_update_permissions (client, 1, wp_proxy_get_bound_id (WP_PROXY (node)),
                                    allowed ? PW_PERM_ALL : 0);

      g_value_unset (&node_value);
    }
}

static void
update_camera_permissions (XdpWpClientManager *self,
                           WpClient           *client,
                           GVariant           *permissions)
{
  g_autoptr(GVariant) perms = NULL;
  const char *app_id = NULL;

  if (!permissions)
    {
      g_autoptr(GVariant) data = NULL;
      g_autoptr(GError) error = NULL;

      if (!xdp_dbus_impl_permission_store_call_lookup_sync (self->permission_store,
                                                            "devices",
                                                            "camera",
                                                            &perms,
                                                            &data,
                                                            self->cancellable,
                                                            &error))
        {
          wp_warning_object (self, "Failed to lookup camera permission: %s", error->message);
          return;
        }

      permissions = perms;
    }

  g_assert (permissions != NULL);

  if (client)
    {
      g_autoptr(GVariantIter) value = NULL;

      app_id = wp_pipewire_object_get_property (WP_PIPEWIRE_OBJECT (client),
                                                XDP_PW_KEY_APP_ID);

      if (app_id)
        {
          g_variant_lookup (permissions, app_id, "as", &value);
          update_client_camera_permission (self, client, value);
          wp_info_object (client, "Camera permission for '%s' updated", app_id);
        }
      else
        {
          wp_warning_object (client, "Client has no app id set");
        }
    }
  else
    {
      g_autoptr(WpIterator) iter = NULL;
      g_auto(GValue) iter_value = G_VALUE_INIT;

      iter =
        wp_object_manager_new_filtered_iterator_full (self->client_om,
                                                      wp_object_interest_ref (self->camera_scope));
      while (wp_iterator_next (iter, &iter_value))
        {
          WpClient *client = g_value_get_object (&iter_value);
          g_autoptr(GVariantIter) value = NULL;

          app_id = wp_pipewire_object_get_property (WP_PIPEWIRE_OBJECT (client),
                                                    XDP_PW_KEY_APP_ID);

          if (app_id)
            {
              g_variant_lookup (permissions, app_id, "as", &value);
              update_client_camera_permission (self, client, value);
              wp_info_object (client, "Camera permission for '%s' updated", app_id);
            }
          else
            {
              wp_warning_object (client, "Client has no app id set");
            }

          g_value_unset (&iter_value);
        }
    }
}

static void
on_permission_changed_cb (XdpWpClientManager         *self,
                          const char                 *arg_table,
                          const char                 *arg_id,
                          gboolean                    arg_deleted,
                          GVariant                   *arg_data,
                          GVariant                   *arg_permissions,
                          XdpDbusImplPermissionStore *permission_store)
{
  if (g_strcmp0 (arg_table, "devices") != 0 || g_strcmp0 (arg_id, "camera") != 0)
    return;

  update_camera_permissions (self, NULL, arg_permissions);
}

static void
on_dbus_connection_plugin_state_changed (XdpWpClientManager *self,
                                         GParamSpec         *pspec,
                                         GObject            *object)
{
  WpDBusConnectionState state = -1;
  g_autoptr(GDBusConnection) connection = NULL;
  g_autoptr(GError) error = NULL;

  g_object_get (self->dbus_connection_plugin, "state", &state, NULL);

  if (state != WP_DBUS_CONNECTION_STATE_CONNECTED)
    {
      g_clear_signal_handler (&self->permission_changed_signal_id, self->permission_store);
      g_clear_object (&self->permission_store);

      wp_debug_object (self, "Permission store proxy cleared");
      return;
    }

  g_object_get (self->dbus_connection_plugin, "connection", &connection, NULL);
  g_return_if_fail (connection != NULL);

  self->permission_store =
    xdp_dbus_impl_permission_store_proxy_new_sync (connection,
                                                   G_DBUS_PROXY_FLAGS_NONE,
                                                   "org.freedesktop.impl.portal.PermissionStore",
                                                   "/org/freedesktop/impl/portal/PermissionStore",
                                                   NULL,
                                                   &error);

  if (!self->permission_store)
    {
      wp_warning_object (self,
                         "Failed to create permission store proxy: %s",
                         error->message);
      return;
    }

  self->permission_changed_signal_id = g_signal_connect_swapped (self->permission_store,
                                                                 "changed",
                                                                 G_CALLBACK (on_permission_changed_cb),
                                                                 self);

  wp_debug_object (self, "Permission store proxy created");
}

static void
on_camera_added_cb (XdpWpClientManager *self,
                    WpNode             *node,
                    WpObjectManager    *manager)
{
  if (self->permission_store)
    update_camera_permissions (self, NULL, NULL);
}

static void
on_client_added_cb (XdpWpClientManager *self,
                    WpClient           *client,
                    WpObjectManager    *manager)
{
  wp_client_update_permissions (client, 1, self->node_factory_id, PW_PERM_R);

  if (wp_object_interest_matches (self->camera_scope, client))
    {
      if (!(self->camera_om && self->permission_store))
        {
          wp_info_object (self,
                          "Granting ALL access to client %u",
                          wp_proxy_get_bound_id (WP_PROXY (client)));
          wp_client_update_permissions (client, 1, PW_ID_ANY, PW_PERM_ALL);
        }
      else
        {
          update_camera_permissions (self, client, NULL);
        }
    }
}

static void
on_node_factory_added_cb (XdpWpClientManager *self,
                          WpFactory          *factory,
                          WpObjectManager    *manager)
{
  g_autoptr(WpObjectManager) om = manager;

  g_assert (WP_IS_FACTORY (factory));

  self->node_factory_id = wp_proxy_get_bound_id (WP_PROXY (factory));
  g_assert (self->node_factory_id != 0);

  wp_debug_object (self, "Node factory id: %d", self->node_factory_id);

  self->client_added_signal_id = g_signal_connect_swapped (self->client_om,
                                                           "object-added",
                                                           G_CALLBACK (on_client_added_cb),
                                                           self);

  self->camera_added_signal_id = g_signal_connect_swapped (self->camera_om,
                                                           "object-added",
                                                           G_CALLBACK (on_camera_added_cb),
                                                           self);

  wp_core_install_object_manager (self->core, self->client_om);
}

static void
xdp_wp_client_manager_set_property (GObject      *object,
                                    guint         property_id,
                                    const GValue *value,
                                    GParamSpec   *pspec)
{
  XdpWpClientManager *self = XDP_WP_CLIENT_MANAGER (object);

  switch ((XdpWpClientManagerProps) property_id)
    {
    case PROP_CORE:
      self->core = g_object_ref (g_value_get_object (value));
      break;

    case PROP_CAMERA_OM:
      self->camera_om = g_object_ref (g_value_get_object (value));
      break;
    }
}

static void
xdp_wp_client_manager_constructed (GObject *object)
{
  XdpWpClientManager *self = XDP_WP_CLIENT_MANAGER (object);
  g_autoptr(WpObjectInterest) interest = NULL;
  WpObjectManager *node_factory_om;

  G_OBJECT_CLASS (xdp_wp_client_manager_parent_class)->constructed (object);

  g_assert (WP_IS_CORE (self->core));

  self->dbus_connection_plugin = wp_plugin_find (self->core, "dbus-connection");
  if (!self->dbus_connection_plugin)
    {
    wp_critical_object (self, "dbus-connection module must be loaded before xdp-desktop-portal");
    }
  else
    {
      self->dbus_changed_signal_id =
      g_signal_connect_swapped (self->dbus_connection_plugin,
                                "notify::state",
                                G_CALLBACK (on_dbus_connection_plugin_state_changed),
                                self);
      on_dbus_connection_plugin_state_changed (self, NULL, NULL);
    }

  interest = wp_object_interest_new_type (WP_TYPE_FACTORY);
  wp_object_interest_add_constraint (interest,
                                     WP_CONSTRAINT_TYPE_PW_PROPERTY,
                                     PW_KEY_FACTORY_TYPE_NAME,
                                     WP_CONSTRAINT_VERB_EQUALS,
                                     g_variant_new_string ("PipeWire:Interface:ClientNode"));
  g_assert (wp_object_interest_validate (interest, NULL));

  node_factory_om = wp_object_manager_new (); /* Destroyed once the factory is added */
  wp_object_manager_add_interest_full (node_factory_om, g_steal_pointer (&interest));

  interest = wp_object_interest_new_type (WP_TYPE_CLIENT);
  wp_object_interest_add_constraint (interest,
                                     WP_CONSTRAINT_TYPE_PW_PROPERTY,
                                     PW_KEY_ACCESS,
                                     WP_CONSTRAINT_VERB_EQUALS,
                                     g_variant_new_string (XDP_PW_ACCESS));
  wp_object_interest_add_constraint (interest,
                                     WP_CONSTRAINT_TYPE_PW_PROPERTY,
                                     XDP_PW_KEY_APP_ID,
                                     WP_CONSTRAINT_VERB_IS_PRESENT,
                                     NULL);
  wp_object_interest_add_constraint (interest,
                                     WP_CONSTRAINT_TYPE_PW_PROPERTY,
                                     XDP_PW_KEY_SCOPE,
                                     WP_CONSTRAINT_VERB_IS_PRESENT,
                                     NULL);
  g_assert (wp_object_interest_validate (interest, NULL));

  self->client_om = wp_object_manager_new ();
  wp_object_manager_add_interest_full (self->client_om, g_steal_pointer (&interest));

  g_signal_connect_swapped (node_factory_om,
                            "object-added",
                            G_CALLBACK (on_node_factory_added_cb),
                            self);

  wp_core_install_object_manager (self->core, node_factory_om);
}

static void
xdp_wp_client_manager_dispose (GObject *object)
{
  XdpWpClientManager *self = XDP_WP_CLIENT_MANAGER (object);

  g_clear_signal_handler (&self->camera_added_signal_id, self->camera_om);
  g_clear_signal_handler (&self->client_added_signal_id, self->client_om);

  g_clear_signal_handler (&self->permission_changed_signal_id, self->permission_store);
  g_clear_signal_handler (&self->dbus_changed_signal_id, self->dbus_connection_plugin);

  g_cancellable_cancel (self->cancellable);

  G_OBJECT_CLASS (xdp_wp_client_manager_parent_class)->dispose (object);
}

static void
xdp_wp_client_manager_finalize (GObject *object)
{
  XdpWpClientManager *self = XDP_WP_CLIENT_MANAGER (object);

  g_clear_object (&self->client_om);
  g_clear_object (&self->camera_om);

  g_clear_object (&self->permission_store);

  g_clear_object (&self->dbus_connection_plugin);

  g_clear_object (&self->core);

  g_clear_pointer (&self->camera_scope, wp_object_interest_unref);

  g_clear_object (&self->cancellable);

  G_OBJECT_CLASS (xdp_wp_client_manager_parent_class)->finalize (object);
}

static void
xdp_wp_client_manager_class_init (XdpWpClientManagerClass *klass)
{
  GObjectClass *object_class = G_OBJECT_CLASS (klass);

  object_class->set_property = xdp_wp_client_manager_set_property;
  object_class->constructed = xdp_wp_client_manager_constructed;
  object_class->dispose = xdp_wp_client_manager_dispose;
  object_class->finalize = xdp_wp_client_manager_finalize;

  props[PROP_CORE] = g_param_spec_object ("core", NULL, NULL,
                                          WP_TYPE_CORE,
                                          G_PARAM_WRITABLE | G_PARAM_CONSTRUCT_ONLY | G_PARAM_STATIC_STRINGS);

  props[PROP_CAMERA_OM] = g_param_spec_object ("camera-om", NULL, NULL,
                                               WP_TYPE_OBJECT_MANAGER,
                                               G_PARAM_WRITABLE | G_PARAM_CONSTRUCT_ONLY | G_PARAM_STATIC_STRINGS);

  g_object_class_install_properties (object_class, G_N_ELEMENTS (props), props);
}

static void
xdp_wp_client_manager_init (XdpWpClientManager *self)
{
  self->cancellable = g_cancellable_new ();

  self->camera_scope = wp_object_interest_new_type (WP_TYPE_CLIENT);
  wp_object_interest_add_constraint (self->camera_scope,
                                     WP_CONSTRAINT_TYPE_PW_PROPERTY,
                                     XDP_PW_KEY_SCOPE,
                                     WP_CONSTRAINT_VERB_MATCHES,
                                     g_variant_new_string ("@(|*,)Camera@(|,*)"));
  g_assert (wp_object_interest_validate (self->camera_scope, NULL));
}

XdpWpClientManager *
xdp_wp_client_manager_new (WpCore          *core,
                           WpObjectManager *camera_om)
{
  return g_object_new (XDP_WP_TYPE_CLIENT_MANAGER,
                       "core", core,
                       "camera-om", camera_om,
                       NULL);
}
