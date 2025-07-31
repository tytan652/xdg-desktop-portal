# SPDX-License-Identifier: LGPL-2.1-or-later
#
# This file is formatted with Python Black

from typing import Any, Dict, Iterator, Optional
from types import ModuleType

import pytest
import dbus
import dbusmock
import os
import sys
import tempfile
import shutil
import subprocess
import time
import signal
from pathlib import Path
from contextlib import chdir

import gi

gi.require_version("UMockdev", "1.0")
from gi.repository import UMockdev  # noqa E402


def pytest_configure() -> None:
    ensure_environment_set()
    ensure_umockdev_loaded()


def pytest_sessionfinish(session, exitstatus):
    # Meson and ginsttest-runner expect tests to exit with status 77 if all
    # tests were skipped
    if exitstatus == pytest.ExitCode.NO_TESTS_COLLECTED:
        session.exitstatus = 77


def ensure_environment_set() -> None:
    env_vars = [
        "XDG_DESKTOP_PORTAL_PATH",
        "XDG_PERMISSION_STORE_PATH",
        "XDG_DOCUMENT_PORTAL_PATH",
    ]

    if not os.getenv("XDP_VALIDATE_AUTO"):
        env_vars += [
            "XDP_VALIDATE_ICON",
            "XDP_VALIDATE_SOUND",
        ]
    else:
        os.environ.pop("XDP_VALIDATE_ICON", None)
        os.environ.pop("XDP_VALIDATE_SOUND", None)

    for env_var in env_vars:
        if not os.getenv(env_var):
            raise Exception(f"{env_var} must be set")


def ensure_umockdev_loaded() -> None:
    umockdev_preload = "libumockdev-preload.so"
    preload = os.environ.get("LD_PRELOAD", "")
    if umockdev_preload not in preload:
        os.environ["LD_PRELOAD"] = f"{umockdev_preload}:{preload}"
        os.execv(sys.executable, [sys.executable] + sys.argv)


def test_dir() -> Path:
    return Path(__file__).resolve().parent


@pytest.fixture
def xdg_desktop_portal_path() -> Path:
    return Path(os.environ["XDG_DESKTOP_PORTAL_PATH"])


@pytest.fixture
def xdg_permission_store_path() -> Path:
    return Path(os.environ["XDG_PERMISSION_STORE_PATH"])


@pytest.fixture
def xdg_document_portal_path() -> Path:
    return Path(os.environ["XDG_DOCUMENT_PORTAL_PATH"])


@pytest.fixture(autouse=True)
def create_test_dirs(umockdev: Optional[UMockdev.Testbed]) -> Iterator[None]:
    # The umockdev argument is to make sure the testbed
    # is created before we create the tmpdir
    env_dirs = [
        "HOME",
        "TMPDIR",
        "XDG_CACHE_HOME",
        "XDG_CONFIG_HOME",
        "XDG_DATA_HOME",
        "XDG_RUNTIME_DIR",
        "XDG_DESKTOP_PORTAL_DIR",
        "PIPEWIRE_CONF_DIR",
        "WIREPLUMBER_CONFIG_DIR",
    ]

    test_root = tempfile.TemporaryDirectory(
        prefix="xdp-testroot-", ignore_cleanup_errors=True
    )

    for env_dir in env_dirs:
        directory = Path(test_root.name) / env_dir.lower()
        directory.mkdir(mode=0o700, parents=True)
        os.environ[env_dir] = directory.absolute().as_posix()

    yield

    test_root.cleanup()


@pytest.fixture
def xdg_data_home_files() -> Dict[str, bytes]:
    """
    Default fixture which can be used to create files in the temporary
    XDG_DATA_HOME directory of the test.
    """
    return {}


@pytest.fixture(autouse=True)
def ensure_xdg_data_home(
    create_test_dirs: Any, xdg_data_home_files: Dict[str, bytes]
) -> None:
    files = xdg_data_home_files
    for name, content in files.items():
        file_path = Path(os.environ["XDG_DATA_HOME"]) / name
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path.absolute().as_posix(), "wb") as f:
            f.write(content)


@pytest.fixture
def xdg_desktop_portal_dir_files() -> Dict[str, bytes]:
    """
    Default fixture which can be used to create files in the temporary
    XDG_DESKTOP_PORTAL_DIR directory of the test.
    """
    return {}


@pytest.fixture
def xdg_desktop_portal_dir_default_files() -> Dict[str, bytes]:
    files = {}

    portals = [
        "org.freedesktop.impl.portal.Access",
        "org.freedesktop.impl.portal.Account",
        "org.freedesktop.impl.portal.AppChooser",
        "org.freedesktop.impl.portal.Background",
        "org.freedesktop.impl.portal.Clipboard",
        "org.freedesktop.impl.portal.DynamicLauncher",
        "org.freedesktop.impl.portal.Email",
        "org.freedesktop.impl.portal.FileChooser",
        "org.freedesktop.impl.portal.GlobalShortcuts",
        "org.freedesktop.impl.portal.Inhibit",
        "org.freedesktop.impl.portal.InputCapture",
        "org.freedesktop.impl.portal.Lockdown",
        "org.freedesktop.impl.portal.Notification",
        "org.freedesktop.impl.portal.Print",
        "org.freedesktop.impl.portal.RemoteDesktop",
        "org.freedesktop.impl.portal.Screenshot",
        "org.freedesktop.impl.portal.Settings",
        "org.freedesktop.impl.portal.Usb",
        "org.freedesktop.impl.portal.Wallpaper",
    ]

    files["test-portals.conf"] = b"""
[preferred]
default=test;
"""

    files["test.portal"] = """
[portal]
DBusName=org.freedesktop.impl.portal.Test
Interfaces={}
""".format(";".join(portals)).encode("utf-8")

    return files


@pytest.fixture(autouse=True)
def ensure_xdg_desktop_portal_dir(
    create_test_dirs: Any,
    xdg_desktop_portal_dir_files: Dict[str, bytes],
    xdg_desktop_portal_dir_default_files: Dict[str, bytes],
) -> None:
    files = xdg_desktop_portal_dir_default_files | xdg_desktop_portal_dir_files
    for name, content in files.items():
        file_path = Path(os.environ["XDG_DESKTOP_PORTAL_DIR"]) / name
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path.absolute().as_posix(), "wb") as f:
            f.write(content)


@pytest.fixture(autouse=True)
def create_test_dbus() -> Iterator[dbusmock.DBusTestCase]:
    bus = dbusmock.DBusTestCase()
    bus.setUp()
    bus.start_session_bus()
    bus.start_system_bus()

    yield bus

    bus.tearDown()
    bus.tearDownClass()


@pytest.fixture(autouse=True)
def create_dbus_monitor(create_test_dbus) -> Iterator[Optional[subprocess.Popen]]:
    if not os.getenv("XDP_DBUS_MONITOR"):
        yield None
        return

    dbus_monitor = subprocess.Popen(["dbus-monitor", "--session"])

    yield dbus_monitor

    dbus_monitor.terminate()
    dbus_monitor.wait()


def _get_server_for_module(
    busses: dict[dbusmock.BusType, dict[str, dbusmock.SpawnedMock]],
    module: ModuleType,
    bustype: dbusmock.BusType,
) -> dbusmock.SpawnedMock:
    assert bustype in dbusmock.BusType

    try:
        return busses[bustype][module.BUS_NAME]
    except KeyError:
        server = dbusmock.SpawnedMock.spawn_for_name(
            module.BUS_NAME,
            "/dbusmock",
            dbusmock.OBJECT_MANAGER_IFACE,
            bustype,
            stdout=None,
            stderr=None,
        )

        busses[bustype][module.BUS_NAME] = server
        return server


def _get_main_obj_for_module(
    server: dbusmock.SpawnedMock, module: ModuleType, bustype: dbusmock.BusType
) -> dbusmock.DBusMockObject:
    try:
        server.obj.AddObject(
            module.MAIN_OBJ,
            "com.example.EmptyInterface",
            {},
            [],
            dbus_interface=dbusmock.MOCK_IFACE,
        )
    except Exception:
        pass

    bustype.wait_for_bus_object(module.BUS_NAME, module.MAIN_OBJ)
    bus = bustype.get_connection()
    return bus.get_object(module.BUS_NAME, module.MAIN_OBJ)


def _terminate_mock_p(process: subprocess.Popen) -> None:
    process.terminate()
    process.wait()


def _terminate_servers(
    busses: dict[dbusmock.BusType, dict[str, dbusmock.SpawnedMock]],
) -> None:
    for server in busses[dbusmock.BusType.SYSTEM].values():
        _terminate_mock_p(server.process)
    for server in busses[dbusmock.BusType.SESSION].values():
        _terminate_mock_p(server.process)


def _start_template(
    busses: dict[dbusmock.BusType, dict[str, dbusmock.SpawnedMock]],
    template: str,
    bus_name: Optional[str],
    params: Dict[str, Any] = {},
) -> None:
    """
    Start the template and potentially start a server for it
    """
    module_path_dir = (test_dir()).parent.absolute().as_posix()
    template_path = test_dir() / f"templates/{template.lower()}.py"
    template = template_path.absolute().as_posix()

    # we cd to the parent dir of the test_dir so that the module search path for
    # the templates is the same as opening the modules from here
    with chdir(module_path_dir):
        module = dbusmock.mockobject.load_module(template)
        bustype = (
            dbusmock.BusType.SYSTEM if module.SYSTEM_BUS else dbusmock.BusType.SESSION
        )

        if bus_name:
            module.BUS_NAME = bus_name

        server = _get_server_for_module(busses, module, bustype)
        main_obj = _get_main_obj_for_module(server, module, bustype)

        main_obj.AddTemplate(
            template,
            dbus.Dictionary(params, signature="sv"),
            dbus_interface=dbusmock.MOCK_IFACE,
        )


@pytest.fixture
def template_params() -> dict[str, dict[str, Any]]:
    """
    Default fixture for overriding the parameters which should be passed to the
    mocking templates. Use required_templates to specify the default parameters
    and override it for specific test cases via

        @pytest.mark.parametrize("template_params", ({"Template": {"foo": "bar"}},))

    """
    return {}


@pytest.fixture
def required_templates() -> dict[str, dict[str, Any]]:
    """
    Default fixture for enumerating the mocking templates the test case requires
    to be started. This is a map from a template spec to the parameters which
    should be passed to the template. The template spec is the name of a
    template in the templates directory and an optional dbus bus name to start
    the template at, separated by a colon.

    This starts the `settings` and `email` templates on the bus names specified
    with BUS_NAME in the templates and passes parameters to settings.

        {
            "settings": {
                "some_param": true,
            },
            "email": {},
        }

    This starts two instances of the settings template with their own parameters
    once on the bus name specified in BUS_NAME in the template, and once on the
    bus name `org.freedesktop.impl.portal.OtherImpl`.

        {
            "settings": {
                "some_param": true,
            },
            "settings:org.freedesktop.impl.portal.OtherImpl": {
                "some_param": false,
            },
        }

    """
    return {}


@pytest.fixture
def templates(
    required_templates: dict[str, dict[str, Any]],
    template_params: dict[str, dict[str, Any]],
) -> Iterator[None]:
    """
    Fixture which starts the required templates with their parameters. Usually
    the `portals` fixture is what you're looking for because it also starts
    the portal frontend and the permission store.
    """
    busses: dict[dbusmock.BusType, dict[str, dbusmock.SpawnedMock]] = {
        dbusmock.BusType.SYSTEM: {},
        dbusmock.BusType.SESSION: {},
    }
    for template_data, params in required_templates.items():
        template, bus_name = (template_data.split(":") + [None])[:2]
        assert template
        params = template_params.get(template, params)
        _start_template(busses, template, bus_name, params)
    yield
    _terminate_servers(busses)


@pytest.fixture
def xdp_overwrite_env() -> dict[str, str]:
    """
    Default fixture which can be used to override the environment that gets
    passed to xdg-desktop-portal, xdg-document-portal and xdg-permission-store.
    """
    return {}


@pytest.fixture
def app_id() -> str:
    """
    Default fixture which can be used to override the app id that the portal
    frontend will discover for incoming connections.
    """
    return "org.example.Test"


@pytest.fixture
def xdp_env(
    xdp_overwrite_env: dict[str, str],
    app_id: str,
    usb_queries: Optional[str],
    umockdev: Optional[UMockdev.Testbed],
) -> dict[str, str]:
    env = os.environ.copy()
    env["G_DEBUG"] = "fatal-criticals"
    env["XDG_CURRENT_DESKTOP"] = "test"

    if app_id:
        env["XDG_DESKTOP_PORTAL_TEST_APP_ID"] = app_id

    if usb_queries:
        env["XDG_DESKTOP_PORTAL_TEST_USB_QUERIES"] = usb_queries

    if umockdev:
        env["UMOCKDEV_DIR"] = umockdev.get_root_dir()

    asan_suppression = test_dir() / "asan.suppression"
    if not asan_suppression.exists():
        raise FileNotFoundError(f"{asan_suppression} does not exist")
    env["LSAN_OPTIONS"] = f"suppressions={asan_suppression}"

    for key, val in xdp_overwrite_env.items():
        env[key] = val

    return env


def _maybe_add_asan_preload(executable: Path, env: dict[str, str]) -> None:
    # ASAN really wants to be the first library to get loaded but we also
    # LD_PRELOAD umockdev and LD_PRELOAD gets loaded before any "normally"
    # linked libraries. This uses ldd to find the version of libasan.so that
    # should be loaded and puts it in front of LD_PRELOAD.
    # This way, LD_PRELOAD and ASAN can be used at the same time.
    ldd = subprocess.check_output(["ldd", executable]).decode("utf-8")
    libs = [line.split()[0] for line in ldd.splitlines()]
    try:
        libasan = next(filter(lambda lib: lib.startswith("libasan"), libs))
    except StopIteration:
        return

    preload = env.get("LD_PRELOAD", "")
    env["LD_PRELOAD"] = f"{libasan}:{preload}"


@pytest.fixture
def xdg_desktop_portal(
    dbus_con: dbus.Bus, xdg_desktop_portal_path: Path, xdp_env: dict[str, str]
) -> Iterator[subprocess.Popen]:
    """
    Fixture which starts and eventually stops xdg-desktop-portal
    """
    if not xdg_desktop_portal_path.exists():
        raise FileNotFoundError(f"{xdg_desktop_portal_path} does not exist")

    env = xdp_env.copy()
    _maybe_add_asan_preload(xdg_desktop_portal_path, env)

    xdg_desktop_portal = subprocess.Popen([xdg_desktop_portal_path], env=env)

    while not dbus_con.name_has_owner("org.freedesktop.portal.Desktop"):
        returncode = xdg_desktop_portal.poll()
        if returncode is not None:
            raise subprocess.SubprocessError(
                f"xdg-desktop-portal exited with {returncode}"
            )
        time.sleep(0.1)

    yield xdg_desktop_portal

    xdg_desktop_portal.send_signal(signal.SIGHUP)
    returncode = xdg_desktop_portal.wait()
    assert returncode == 0


@pytest.fixture
def xdg_permission_store(
    dbus_con: dbus.Bus, xdg_permission_store_path: Path, xdp_env: dict[str, str]
) -> Iterator[subprocess.Popen]:
    """
    Fixture which starts and eventually stops xdg-permission-store
    """
    if not xdg_permission_store_path.exists():
        raise FileNotFoundError(f"{xdg_permission_store_path} does not exist")

    env = xdp_env.copy()
    _maybe_add_asan_preload(xdg_permission_store_path, env)

    permission_store = subprocess.Popen([xdg_permission_store_path], env=env)

    while not dbus_con.name_has_owner("org.freedesktop.impl.portal.PermissionStore"):
        returncode = permission_store.poll()
        if returncode is not None:
            raise subprocess.SubprocessError(
                f"xdg-permission-store exited with {returncode}"
            )
        time.sleep(0.1)

    yield permission_store

    permission_store.send_signal(signal.SIGHUP)
    permission_store.wait()
    # The permission store does not shut down cleanly currently
    # returncode = permission_store.wait()
    # assert returncode == 0


@pytest.fixture
def xdg_document_portal(
    dbus_con: dbus.Bus, xdg_document_portal_path: Path, xdp_env: dict[str, str]
) -> Iterator[subprocess.Popen]:
    """
    Fixture which starts and eventually stops xdg-document-portal
    """
    if not xdg_document_portal_path.exists():
        raise FileNotFoundError(f"{xdg_document_portal_path} does not exist")

    # FUSE and LD_PRELOAD don't like each other. Not sure what exactly is going
    # wrong but it usually just results in a weird hang that needs SIGKILL
    env = xdp_env.copy()
    env.pop("LD_PRELOAD", None)

    document_portal = subprocess.Popen([xdg_document_portal_path], env=env)

    while not dbus_con.name_has_owner("org.freedesktop.portal.Documents"):
        returncode = document_portal.poll()
        if returncode is not None:
            raise subprocess.SubprocessError(
                f"xdg-document-portal exited with {returncode}"
            )
        time.sleep(0.1)

    yield document_portal

    document_portal.send_signal(signal.SIGHUP)
    returncode = document_portal.wait()
    assert returncode == 0


@pytest.fixture
def portals(templates: Any, xdg_desktop_portal: Any, xdg_permission_store: Any) -> None:
    """
    Fixture which starts the required templates, xdg-desktop-portal,
    xdg-document-portal and xdg-permission-store. Most tests require this.
    """
    return None


@pytest.fixture
def usb_queries() -> Optional[str]:
    """
    Default fixture providing the usb queries the connecting process can
    enumerate
    """
    return None


@pytest.fixture
def umockdev() -> Optional[UMockdev.Testbed]:
    """
    Default fixture providing a umockdev testbed
    """
    return None


@pytest.fixture
def dbus_con(create_test_dbus: dbusmock.DBusTestCase) -> dbus.Bus:
    """
    Default fixture which provides the python-dbus session bus of the test.
    """
    con = create_test_dbus.get_dbus(system_bus=False)
    assert con
    return con


@pytest.fixture
def dbus_con_sys(create_test_dbus: dbusmock.DBusTestCase) -> dbus.Bus:
    """
    Default fixture which provides the python-dbus system bus of the test.
    """
    con_sys = create_test_dbus.get_dbus(system_bus=True)
    assert con_sys
    return con_sys


@pytest.fixture
def pipewire_conf_dir_files() -> Dict[str, bytes]:
    """
    Default fixture which can be used to create files in the temporary
    PIPEWIRE_CONF_DIR directory of the test.
    """
    return {}


@pytest.fixture
def pipewire_conf_dir_default_files() -> Dict[str, bytes]:
    files = {}

    files["pipewire.conf"] = b"""
context.properties = {
    core.daemon = true
    core.name   = pipewire-0
}

context.spa-libs = {}

context-modules = [
    { name = libpipewire-module-protocol-native }

    { name = libpipewire-module-metadata }

    { name = libpipewire-module-spa-device-factory }

    { name = libpipewire-module-spa-node-factory }

    { name = libpipewire-module-client-node }

    { name = libpipewire-module-client-device }

    { name = libpipewire-module-portal }

    { name = libpipewire-module-access }

    { name = libpipewire-module-link-factory }

    { name = libpipewire-module-session-manager }
]
"""

    files["client.conf"] = b"""
context.properties = {
    log.level = 0
}

context.spa-libs = {}

context.modules = {
  { name = libpipewire-module-protocol-native }

  { name = libpipewire-module-client-node }

  { name = libpipewire-module-client-device }

  { name = libpipewire-module-metadata }

  { name = libpipewire-module-session-manager }
}
"""

    return files


@pytest.fixture(autouse=True)
def ensure_pipewire_conf_dir(
    create_test_dirs: Any,
    pipewire_conf_dir_files: Dict[str, bytes],
    pipewire_conf_dir_default_files: Dict[str, bytes],
) -> None:
    files = pipewire_conf_dir_default_files | pipewire_conf_dir_files
    for name, content in files.items():
        file_path = Path(os.environ["PIPEWIRE_CONF_DIR"]) / name
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path.absolute().as_posix(), "wb") as f:
            f.write(content)


@pytest.fixture
def pipewire(dbus_con: Any) -> Iterator[subprocess.Popen]:
    """
    Fixture which starts and eventually stops PipeWire
    """
    exec = shutil.which("pipewire")
    if exec is None:
        raise shutil.Error("pipewire path was not found")

    pipewire = subprocess.Popen([exec])

    while not (Path(os.environ["XDG_RUNTIME_DIR"]) / "pipewire-0").exists():
        returncode = pipewire.poll()
        if returncode is not None:
            raise subprocess.SubprocessError(
                f"PipeWire daemon exited with {returncode}"
            )
        time.sleep(0.1)

    yield pipewire

    pipewire.send_signal(signal.SIGINT)
    returncode = pipewire.wait()
    assert returncode == 0


@pytest.fixture
def wireplumber_conf_dir_files() -> Dict[str, bytes]:
    """
    Default fixture which can be used to create files in the temporary
    WIREPLUMBER_CONFIG_DIR directory of the test.
    """
    return {}


@pytest.fixture
def wireplumber_conf_dir_default_files() -> Dict[str, bytes]:
    files = {}

    files["wireplumber.conf"] = b"""
context.spa-libs = {}

context.modules = [
  { name = libpipewire-module-protocol-native }

  { name = libpipewire-module-metadata }
]

wireplumber.profiles = {
  base = {
    check.no-media-session = required
    support.settings = required
    support.log-settings = required
    support.session-services = required
  }

  main = {
    inherits = [ base ]

    metadata.sm-settings = required
    metadata.sm-objects = required

    policy.minimized = required
  }
}

wireplumber.components = [
  {
    name = ensure-no-media-session, type = built-in
    provides = chech.no-media-session
    before = [ support.session-services ]
  }

  {
    name = libwireplumber-module-settings, type = module
    arguments = { metadata.name = sm-settings }
    provides = metadata.sm-settings
    before = [ support.session-services ]
  }

  {
    name = settings-instance, type = built-in
    arguments = { metadata.name = sm-settings }
    provides = support.settings
    after = [ metadata.sm-settings ]
    before = [ support.session-services ]
  }

  {
    name = libwireplumber-module-log-settings, type = module
    provides = support.log-settings
    before = [ support.session-services ]
  }

  {
    name = libwireplumber-module-lua-scripting, type = module
    provides = support.lua-scripting
    before = [ support.session-services ]
  }

  {
    name = libwireplumber-module-standard-event-source, type = module
    provides = support.standard-event-source
    before = [ support.session-services ]
  }

  {
    name = libwireplumber-module-dbus-connection, type = module
    provides = support.dbus
    before = [ support.session-services ]
  }

  {
    name = libwireplumber-module-portal-permissionstore, type = module
    provides = support.portal-permissionstore
    requires = [ support.dbus ]
    before = [ support.session-services ]
  }

  {
    name = libwireplumber-module-si-node, type = module
    provides = si.node
    before = [ support.session-services ]
  }
  {
    name = libwireplumber-module-si-audio-adapter, type = module
    provides = si.audio-adapter
    before = [ support.session-services ]
  }
  {
    name = libwireplumber-module-si-standard-link, type = module
    provides = si.standard-link
    before = [ support.session-services ]
  }

  {
    name = libwireplumber-module-default-nodes-api, type = module
    provides = api.default-nodes
    before = [ support.session-services ]
  }

  {
    name = sm-objects.lua, type = script/lua
    provides = metadata.sm-objects
    requires = [ support.lua-scripting ]
    before = [ support.session-services ]
  }

  {
    name = session-services.lua, type = script/lua
    provides = support.session-services
    requires = [ support.lua-scripting ]
  }

  {
    name = client/access-default.lua, type = script/lua
    provides = script.client.access-default
    requires = [ support.lua-scripting ]
    before = [ support.session-services ]
  }
  {
    name = client/access-portal.lua, type = script/lua
    provides = script.client.access-portal
    requires = [ support.portal-permissionstore, support.lua-scripting ]
    before = [ support.session-services ]
  }
  {
    type = virtual, provides = policy.client.access
    requires = [ script.client.access-default,
                 script.client.access-portal ]
    before = [ support.session-services ]
  }

  {
    name = linking/rescan.lua, type = script/lua
    provides = hooks.linking.rescan
    requires = [ support.lua-scripting ]
    before = [ support.session-services,
               support.standard-event-source ]
  }
  {
    name = linking/prepare-link.lua, type = script/lua
    provides = hooks.linking.target.prepare-link
    requires = [ api.default-nodes, support.lua-scripting ]
    before = [ support.session-services,
               support.standard-event-source ]
  }
  {
    name = linking/link-target.lua, type = script/lua
    provides = hooks.linking.target.link
    requires = [ si.standard-link, support.lua-scripting ]
    before = [ support.session-services,
               support.standard-event-source ]
  }

  {
    type = virtual, provides = policy.linking.standard
    requires = [ hooks.linking.rescan,
                 hooks.linking.target.prepare-link,
                 hooks.linking.target.link ]
    before = [ support.session-services ]
  }

  {
    name = node/create-item.lua, type = script/lua
    provides = hooks.node.create-session-item
    requires = [ si.audio-adapter,
                 si.node,
                 support.lua-scripting ]
    before = [ support.session-services,
               support.standard-event-source ]
  }
  {
    name = node/suspend-node.lua, type = script/lua
    provides = hooks.node.suspend
    requires = [ support.lua-scripting ]
    before = [ support.session-services,
               support.standard-event-source ]
  }
  {
    name = node/state-stream.lua, type = script/lua
    provides = hooks.stream.state
    requires = [ support.lua-scripting ]
    before = [ support.session-services,
               support.standard-event-source ]
  }
  {
    name = node/filter-forward-format.lua, type = script/lua
    provides = hooks.filter.forward-format
    requires = [ support.lua-scripting ]
    before = [ support.session-services,
               support.standard-event-source ]
  }
  {
    type = virtual, provides = policy.node
    requires = [ hooks.node.create-session-item ]
    wants = [ hooks.node.suspend
              hooks.stream.state
              hooks.filter.forward-format ]
    before = [ support.session-services ]
  }

  {
    type = virtual, provides = policy.minimized
    requires = [ policy.client.access
                 policy.linking.standard
                 policy.node
                 support.standard-event-source ]
    before = [ support.session-services ]
  }
]

wireplumber.settings.schema = {
  linking.allow-moving-streams = {
    name = "Allow moving streams"
    description = "Streams may be moved by adding PipeWire metadata at runtime"
    type = "bool"
    default = true
  }

  node.stream.restore-props = {
    name = "Restore properties"
    description = "Remember and restore properties of streams"
    type = "bool"
    default = true
  }
  node.stream.restore-target = {
    name = "Restore target"
    description = "Remember and restore stream targets"
    type = "bool"
    default = true
  }
}
"""

    return files


@pytest.fixture(autouse=True)
def ensure_wireplumber_conf_dir(
    create_test_dirs: Any,
    wireplumber_conf_dir_files: Dict[str, bytes],
    wireplumber_conf_dir_default_files: Dict[str, bytes],
) -> None:
    files = wireplumber_conf_dir_default_files | wireplumber_conf_dir_files
    for name, content in files.items():
        file_path = Path(os.environ["WIREPLUMBER_CONFIG_DIR"]) / name
        file_path.parent.mkdir(parents=True, exist_ok=True)
        with open(file_path.absolute().as_posix(), "wb") as f:
            f.write(content)


@pytest.fixture
def wireplumber(pipewire: Any) -> Iterator[subprocess.Popen]:
    """
    Fixture which starts and eventually stops WirePlumber
    """
    exec = shutil.which("wireplumber")
    if exec is None:
        raise shutil.Error("wireplumber path was not found")

    wireplumber = subprocess.Popen([exec])

    time.sleep(1)  # FIXME: ensure the process is done initializing

    yield wireplumber

    wireplumber.send_signal(signal.SIGINT)
    returncode = wireplumber.wait()
    assert returncode == 0
