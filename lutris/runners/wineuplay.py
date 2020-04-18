"""Uplay for Windows runner"""
import os
import time

from lutris import settings
from lutris.runners import wine
from lutris.command import MonitoredCommand
from lutris.util import system
from lutris.util.log import logger
from lutris.util.strings import split_arguments
from lutris.util.yaml import read_yaml
from lutris.util.wine.registry import WineRegistry
from lutris.util.wine.wine import WINE_DEFAULT_ARCH
from lutris.runners.commands.wine import (  # noqa pylint: disable=unused-import
    set_regedit,
    set_regedit_file,
    delete_registry_key,
    create_prefix,
    wineexec,
    winetricks,
    winecfg,
    winekill,
    install_cab_component,
)

DX2010_INSTALLER_URL = ("https://lutris.net/files/tools/directx-2010.tar.gz")
# Yanked from Uplay script on lutris.net
UPLAY_INSTALLER_URL = ("https://ubistatic3-a.akamaihd.net/orbit/\
                       launcher_installer/UplayInstaller.exe")


def is_running():
    """Return whether Uplay is running"""
    return bool(system.get_pid("Uplay.exe$"))


def kill():
    """Force kills Uplay"""
    system.kill_pid(system.get_pid("Uplay.exe$"))


# pylint: disable=C0103
class wineuplay(wine.wine):
    description = "Runs Uplay for Windows games"
    multiple_versions = False
    human_name = "Uplay"
    platforms = ["Windows"]
    runnable_alone = True
    depends_on = wine.wine
    default_arch = WINE_DEFAULT_ARCH
    game_options = [
        {
            "option": "game_id",
            "type": "string",
            "label": "Game ID",
            "help": (
                "TODO"
            ),
        },
        {
            "option": "prefix",
            "type": "directory_chooser",
            "label": "Prefix",
            "help": (
                'The prefix (also named "bottle") used by Wine.\n'
                "It's a directory containing a set of files and "
                "folders making up a confined Windows environment."
            ),
        },
        {
            "option": "arch",
            "type": "choice",
            "label": "Prefix architecture",
            "choices": [("Auto", "auto"), ("32-bit", "win32"),
                        ("64-bit", "win64")],
            "default": "auto",
            "help": (
                "The architecture of the Windows environment.\n"
                "32-bit is recommended unless running "
                "a 64-bit only game."
            ),
        },
        {
            "option": "nolaunch",
            "type": "bool",
            "default": False,
            "label": "Do not launch game, only open Uplay",
            "help": (
                "Opens Uplay with the current settings without running the \
                game, "
                "useful if a game has several launch options."
            ),
        },
    ]

    def __init__(self, config=None):
        super(wineuplay, self).__init__(config)
        self.own_game_remove_method = "Remove game data (through Uplay)"
        self.no_game_remove_warning = True
        wineuplay_options = [
            {
                "option": "uplay_path",
                "type": "directory_chooser",
                "label": "Custom Uplay location",
                "help": (
                    "Choose a folder containing Uplay.exe\n"
                    "By default, Lutris will look for a Uplay installation "
                    "into ~/.wine or will install it in its own custom Wine "
                    "prefix."
                ),
            },
            {
                "option": "args",
                "type": "string",
                "label": "Arguments",
                "advanced": True,
                "help": ("Extra command line arguments used when "
                         "launching Uplay"),
            },
            {
                "option": "default_win32_prefix",
                "type": "directory_chooser",
                "label": "Default Wine prefix (32bit)",
                "default": os.path.join(settings.RUNNER_DIR,
                                        "wineuplay/prefix"),
                "help": "Default prefix location for Uplay (32 bit)",
                "advanced": True,
            },
            {
                "option": "default_win64_prefix",
                "type": "directory_chooser",
                "label": "Default Wine prefix (64bit)",
                "default": os.path.join(settings.RUNNER_DIR,
                                        "wineuplay/prefix64"),
                "help": "Default prefix location for Uplay (64 bit)",
                "advanced": True,
            },
        ]
        for option in reversed(wineuplay_options):
            self.runner_options.insert(0, option)

    def __repr__(self):
        return "Wineuplay runner (%s)" % self.config

    @property
    def game_id(self):
        """Uplay game ID used to uniquely identify games"""
        return self.game_config.get("game_id") or ""

    @property
    def prefix_path(self):
        _prefix = self.game_config.get("prefix") \
            or self.get_or_create_default_prefix(
                arch=self.game_config.get("arch")
            )
        return os.path.expanduser(_prefix)

    @property
    def browse_dir(self):
        """Return the path to open with the Browse Files action."""
        if not self.is_installed():
            installed = self.install_dialog()
            if not installed:
                return False
        return self.game_path

    @property
    def game_path(self):
        if not self.game_id:
            return None
        return self.get_game_path_from_game_id(self.game_id)

    @property
    def working_dir(self):
        """Return the working directory to use when running the game."""
        return os.path.expanduser("~/")

    @property
    def launch_args(self):
        """Provide launch arguments for Uplay"""
        uplay_path = self.get_uplay_path()
        if not uplay_path:
            raise RuntimeError("Can't find a Uplay executable")
        return [
            self.get_executable(),
            uplay_path,
        ] + split_arguments(self.runner_config.get("args") or "")

    @staticmethod
    def get_open_command(registry):
        """Return Uplay's Open command, useful for locating uplay when it has
           been installed but not yet launched"""
        value = registry.query("Software/Classes/uplay/Shell/Open/Command",
                               "default")
        if not value:
            return None
        parts = value.split('"')
        return parts[1].strip("\\")

    # TODO: not clear about the correct way to get the prefix
    def get_uplay_config(self):
        """Return Uplay's config.yml as a dict"""
        prefix = self.game_config.get("prefix") \
            or self.get_or_create_default_prefix(
                arch=self.game_config.get("arch")
            )
        uplay_config = os.path.join(prefix, "users", os.getenv("USER"),
                                    "Local Settings/Application Data/Ubisoft \
                                    Game Launcher settings.yml")
        if uplay_config:
            return read_yaml(uplay_config)

    @property
    def uplay_data_dir(self):
        """Return dir where Uplay files lie"""
        uplay_path = self.get_uplay_path()
        if uplay_path:
            uplay_dir = os.path.dirname(uplay_path)
            if os.path.isdir(uplay_dir):
                return uplay_dir

    def get_uplay_path(self):
        """Return Uplay exe's path"""
        custom_path = self.runner_config.get("uplay_path") or ""
        if custom_path:
            custom_path = os.path.abspath(
                os.path.expanduser(os.path.join(custom_path, "Uplay.exe"))
            )
            if system.path_exists(custom_path):
                return custom_path

        candidates = [
            self.get_default_prefix(arch="win64"),
            self.get_default_prefix(arch="win32"),
            os.path.expanduser("~/.wine"),
        ]
        for prefix in candidates:
            # Try the default install path
            for default_path in [
                "drive_c/Program Files (x86)/Ubisoft/Ubisoft Game Launcher/\
                upc.exe",
                "drive_c/Program Files/Ubisoft/Ubisoft Game Launcher/upc.exe",
            ]:
                uplay_path = os.path.join(prefix, default_path)
                if system.path_exists(uplay_path):
                    return uplay_path

            # Try from the registry key
            user_reg = os.path.join(prefix, "user.reg")
            if not system.path_exists(user_reg):
                continue
            registry = WineRegistry(user_reg)
            uplay_path = self.get_open_command(registry)
            if not uplay_path:
                continue
            return system.fix_path_case(registry.get_unix_path(uplay_path))
        return ""

    def install(self, version=None, downloader=None, callback=None):
        dx2010_path = os.path.join(settings.TMP_PATH, "dxsetup.zip")
        installer_path = os.path.join(settings.TMP_PATH, "UplayInstaller.exe")

        def on_uplay_downloaded(*_args):
            prefix = self.get_or_create_default_prefix()

            # Install DirectX DLL's, Microsoft fonts,
            # d3dcompiler_43.dll, and MS GDI+ before installing Uplay
            wineexec(
                dx2010_path,
                args="/silent",
                prefix=prefix,
                wine_path=self.get_executable(),
            )
            winetricks("corefonts d3dcompiler_43 gdiplus",
                       prefix=prefix, wine_path=self.get_executable())
            wineexec(
                installer_path,
                args="/S",
                prefix=prefix,
                wine_path=self.get_executable(),
            )
            if callback:
                callback()

        self.download_and_extract(DX2010_INSTALLER_URL, dx2010_path)
        downloader(UPLAY_INSTALLER_URL, installer_path, on_uplay_downloaded)

    def is_installed(self, version=None, fallback=True, min_version=None):
        """Checks if wine is installed and if the Uplay executable is on the
        drive"""
        if not super().is_installed(
                version=version,
                fallback=fallback,
                min_version=min_version
        ):
            return False
        if not system.path_exists(self.get_default_prefix(
            arch=self.default_arch)
        ):
            return False
        return system.path_exists(self.get_uplay_path())

    # TODO: requires mucking about in the Wine registry
    def get_game_id_list(self):
        """Return the list of game ID's of all user's games"""
        prefix = self.game_config.get("prefix") \
            or self.get_or_create_default_prefix(
                arch=self.game_config.get("arch")
            )
        user_reg = os.path.join(prefix, "user.reg")
        if system.path_exists(user_reg):
            registry = WineRegistry(user_reg)
            if registry:
                apps = registry.query("Software/Ubisoft/Uplay/GameStarter")
                return apps.keys()

    def get_game_path_from_game_id(self, game_id):
        """Return the game directory"""
        prefix = self.game_config.get("prefix") \
            or self.get_or_create_default_prefix(
                arch=self.game_config.get("arch")
            )
        system_reg = os.path.join(prefix, "system.reg")
        registry = WineRegistry(system_reg)
        if registry:
            logger.debug("Checking for game %s in the Wine registry", game_id)
            if self.wine_arch() == "win64":
                game_path = registry.query(
                    "Software/Wow6432/Ubisoft/Launcher/Installs/%s" % game_id,
                    "InstallDir"
                )
            else:
                game_path = registry.query(
                    "Software/Ubisoft/Launcher/Installs/%s" % game_id,
                    "InstallDir"
                )
            if game_path:
                logger.debug("Game found in %s", game_path)
                return game_path
        logger.warning("Data path for game %s not found.", game_id)

    def get_game_dir(self):
        """Return Uplay's default game installation folder."""
        uplay_config = self.get_uplay_config()
        if uplay_config:
            game_dir = uplay_config["misc"]["game_installation_path"]
            return game_dir

    def create_default_prefix(self, prefix_dir, arch=None):
        """Create the default prefix for Uplay

        Not sure Uplay will keep on working on 32bit prefixes for long.

        Args:
            prefix_path (str): Destination of the default prefix
            arch (str): Optional architecture for the prefix, defaults to win64
        """
        logger.debug("Creating default wineuplay prefix")
        arch = arch or self.default_arch

        if not system.path_exists(os.path.dirname(prefix_dir)):
            os.makedirs(os.path.dirname(prefix_dir))
        create_prefix(prefix_dir, arch=arch, wine_path=self.get_executable())

    def get_default_prefix(self, arch):
        """Return the default prefix's path."""
        return self.runner_config["default_%s_prefix" % arch]

    def get_or_create_default_prefix(self, arch=None):
        """Return the default prefix's path. Create it if it doesn't exist"""
        if not arch or arch == "auto":
            arch = self.default_arch
        prefix = self.get_default_prefix(arch=arch)
        if not system.path_exists(prefix):
            self.create_default_prefix(prefix, arch=arch)
        return prefix

    def install_game(self, game_id, generate_acf=False):
        """Install a game with Uplay"""
        if not game_id:
            raise ValueError("Missing game ID in wineuplay.install_game")
        system.execute(
            self.launch_args + ["uplay://install/%s" % game_id],
            env=self.get_env()
        )

    def force_shutdown(self):
        """Forces a Uplay shutdown, double checking its exit status and raising
        an error if it cannot be killed"""

        def has_uplay_shutdown(times=10):
            for _ in range(1, times + 1):
                time.sleep(1)
                if not is_running():
                    return True

        # Stop existing wineuplay to prevent Wine prefix/version problems
        if is_running():
            logger.info("Waiting for Uplay to shutdown...")
            self.shutdown()
            if not has_uplay_shutdown():
                logger.info("Forcing Uplay shutdown")
                kill()
                if not has_uplay_shutdown(5):
                    logger.error("Failed to shut down wineuplay :(")

    def prelaunch(self):
        super().prelaunch()
        try:
            self.force_shutdown()
        except RuntimeError:
            return False
        return True

    def get_run_data(self):
        return {"command": self.launch_args, "env": self.get_env(os_env=False)}

    def get_command(self):
        """Return the command used to launch a Uplay game"""
        command = self.launch_args
        if not self.game_config.get("nolaunch"):
            command.append("uplay://launch/%s" % self.game_id)
        return command

    def play(self):
        """Run a game"""
        if self.runner_config.get("x360ce-path"):
            self.setup_x360ce(self.runner_config["x360ce-path"])
        try:
            return {"env": self.get_env(os_env=False),
                    "command": self.get_command()}
        except FileNotFoundError as ex:
            return {"error": "FILE_NOT_FOUND", "file": ex.filename}

    def remove_game_data(self, game_id=None, **kwargs):
        """Uninstall a game from Uplay"""
        if not self.is_installed():
            logger.warning(
                "Trying to remove a wineuplay game but it's not installed."
            )
            return False
        self.force_shutdown()
        uninstall_command = MonitoredCommand(
            (self.launch_args + ["uplay://uninstall/%s"
                                 % (game_id or self.game_id)]),
            runner=self,
            env=self.get_env(os_env=False),
        )
        uninstall_command.start()
