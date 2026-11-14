/* SPDX-License-Identifier: LGPL-2.1-or-later */
/* SPDX-FileCopyrightText: Copyright © the xdg-desktop-portal contributors */

#pragma once

#include <gio/gio.h>
#include <glib-object.h>
#include <wp/wp.h>

#define XDP_WP_TYPE_CLIENT_MANAGER (xdp_wp_client_manager_get_type())
G_DECLARE_FINAL_TYPE (XdpWpClientManager, xdp_wp_client_manager, XDP_WP, CLIENT_MANAGER, GObject);

XdpWpClientManager * xdp_wp_client_manager_new (WpCore           *core,
                                                WpObjectManager  *camera_om);
