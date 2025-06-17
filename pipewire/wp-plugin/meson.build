xdp_wireplumber_module_sources = [
  'xdp-wp-permission-manager.c',
  'xdp-wp-plugin.c',
]

xdp_wireplumber_module_sources += impl_built_sources

xdp_wireplumber_module = shared_library(
  'xdg-desktop-portal',
  xdp_wireplumber_module_sources,
  dependencies: [wireplumber_dep, pipewire_dep],
  install: true,
  install_dir: wireplumber_module_dir,
)
