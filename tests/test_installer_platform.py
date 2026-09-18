import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INSTALLER = ROOT / "install.sh"
COMPOSE = ROOT / "docker-compose.standalone.yml"


def run_installer_function(body, *args):
    return subprocess.run(
        [
            "/bin/bash",
            "-c",
            f'source "$1"; {body}',
            "installer-test",
            str(INSTALLER),
            *map(str, args),
        ],
        check=True,
        capture_output=True,
        text=True,
    )


class InstallerPlatformTests(unittest.TestCase):
    def test_readmes_use_single_line_platform_install_commands(self):
        readmes = [ROOT / "README.md", ROOT / "README.zh-CN.md"]
        commands = [
            "curl -fsSL https://raw.githubusercontent.com/oneprolabs/"
            "sourcelens/main/install.sh | sudo bash",
            "curl -fsSL https://gitee.com/oneprolabs/sourcelens/raw/"
            "main/install.sh | sudo bash -s -- --channel cn "
            "--download-source gitee",
            "curl -fsSL https://raw.githubusercontent.com/oneprolabs/"
            "sourcelens/main/install.sh | bash",
        ]
        for readme in readmes:
            content = readme.read_text()
            for command in commands:
                with self.subTest(readme=readme.name, command=command):
                    self.assertIn(command, content)

    def test_readmes_explain_windows_china_channel_without_sudo(self):
        readmes = [ROOT / "README.md", ROOT / "README.zh-CN.md"]
        for readme in readmes:
            content = readme.read_text()
            with self.subTest(readme=readme.name):
                self.assertIn("`sudo bash`", content)
                self.assertIn("`bash`", content)

    def test_readmes_document_noninteractive_install_and_options(self):
        readmes = [ROOT / "README.md", ROOT / "README.zh-CN.md"]
        expected = [
            "oneprolabs/sourcelens",
            "--download-source gitee --yes",
            "--dir /srv/sourcelens",
            "--port 10083",
            "--https-port 10443",
            "--domain lens.example.com",
            "install.sh --help",
        ]
        for readme in readmes:
            content = readme.read_text()
            for text in expected:
                with self.subTest(readme=readme.name, text=text):
                    self.assertIn(text, content)

    def test_readmes_document_recommended_resources_as_requirements(self):
        requirements = {
            ROOT / "README.md": (
                "At least 4 GB available memory and 20 GB free disk space"
            ),
            ROOT / "README.zh-CN.md": (
                "至少 4 GB 可用内存和 20 GB 可用磁盘空间"
            ),
        }
        for readme, requirement in requirements.items():
            with self.subTest(readme=readme.name):
                self.assertIn(requirement, readme.read_text())

    def test_default_install_dir_is_platform_appropriate(self):
        cases = [
            ("linux", "/opt/sourcelens"),
            ("macos", "/Users/Shared/sourcelens"),
        ]
        for platform, expected in cases:
            with self.subTest(platform=platform):
                result = run_installer_function(
                    'default_install_dir_for_platform "$2"', platform
                )
                self.assertEqual(result.stdout, expected)

        result = run_installer_function(
            'HOME=/c/Users/test; default_install_dir_for_platform windows'
        )
        self.assertEqual(result.stdout, "/c/Users/test/sourcelens")

    def test_windows_does_not_require_root(self):
        result = run_installer_function(
            'PLATFORM=windows; LOG_FILE=""; require_root'
        )

        self.assertIn("Windows Git Bash", result.stdout)

    def test_macos_compose_avoids_postgresql_log_bind_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            compose = Path(directory) / "docker-compose.standalone.yml"
            shutil.copyfile(COMPOSE, compose)

            run_installer_function(
                'PLATFORM=macos; patch_platform_compose "$2"', compose
            )

            content = compose.read_text()
            self.assertNotIn(
                "./data/logs/postgresql:/var/log/postgresql", content
            )
            self.assertIn(
                "./data/postgresql/data:/var/lib/postgresql/data", content
            )

    def test_linux_compose_keeps_postgresql_log_bind_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            compose = Path(directory) / "docker-compose.standalone.yml"
            shutil.copyfile(COMPOSE, compose)

            run_installer_function(
                'PLATFORM=linux; patch_platform_compose "$2"', compose
            )

            content = compose.read_text()
            self.assertIn(
                "./data/logs/postgresql:/var/log/postgresql", content
            )

    def test_windows_compose_uses_docker_volumes_for_databases(self):
        with tempfile.TemporaryDirectory() as directory:
            compose = Path(directory) / "docker-compose.standalone.yml"
            shutil.copyfile(COMPOSE, compose)

            run_installer_function(
                'PLATFORM=windows; patch_platform_compose "$2"', compose
            )

            content = compose.read_text()
            self.assertNotIn(
                "./data/postgresql/data:/var/lib/postgresql/data", content
            )
            self.assertNotIn("./data/redis:/data", content)
            self.assertNotIn("./data/logs/postgresql", content)
            self.assertNotIn("./data/logs/redis", content)
            self.assertIn(
                "postgresql_data:/var/lib/postgresql/data", content
            )
            self.assertIn("redis_data:/data", content)
            self.assertIn("\nvolumes:\n", content)

    def test_redis_healthcheck_uses_stable_working_directory(self):
        content = COMPOSE.read_text()

        self.assertIn("working_dir: /tmp", content)
        self.assertIn("command: redis-server --dir /data", content)

    def test_windows_compose_paths_use_cygpath(self):
        result = run_installer_function(
            'PLATFORM=windows; '
            'cygpath() { printf "C:\\\\sourcelens"; }; '
            'docker_host_path /c/sourcelens'
        )

        self.assertEqual(result.stdout, "C:\\sourcelens")

    def test_windows_generates_certificate_without_docker_mount(self):
        with tempfile.TemporaryDirectory() as directory:
            install_dir = Path(directory)
            run_installer_function(
                'PLATFORM=windows; INSTALL_DIR="$2"; DOMAIN=127.0.0.1; '
                'LOG_FILE="$3"; '
                'docker() { return 1; }; generate_certs',
                install_dir,
                install_dir / "install.log",
            )

            certs = install_dir / "docker/nginx/certs"
            self.assertTrue((certs / "nginx-selfsigned.crt").is_file())
            self.assertTrue((certs / "nginx-selfsigned.key").is_file())

    def test_windows_does_not_require_tls_helper_image(self):
        with tempfile.TemporaryDirectory() as directory:
            run_installer_function(
                'PLATFORM=windows; INSTALL_DIR="$2"; '
                "! tls_helper_image_required",
                directory,
            )

    def test_linux_requires_tls_helper_image_when_certificate_is_missing(self):
        with tempfile.TemporaryDirectory() as directory:
            run_installer_function(
                'PLATFORM=linux; INSTALL_DIR="$2"; '
                "tls_helper_image_required",
                directory,
            )

    def test_windows_disables_msys_path_conversion_for_openssl(self):
        with tempfile.TemporaryDirectory() as directory:
            install_dir = Path(directory)
            run_installer_function(
                'PLATFORM=windows; INSTALL_DIR="$2"; DOMAIN=127.0.0.1; '
                'LOG_FILE="$3"; '
                'openssl() { '
                '[[ "${MSYS_NO_PATHCONV:-}" == "1" ]] || return 2; '
                'while [[ "$#" -gt 0 ]]; do '
                'case "$1" in -keyout|-out) shift; '
                '[[ "$1" != /* ]] || return 3; : >"$1" ;; esac; '
                'shift; done; '
                '}; generate_certs',
                install_dir,
                install_dir / "install.log",
            )

            certs = install_dir / "docker/nginx/certs"
            self.assertTrue((certs / "nginx-selfsigned.crt").is_file())
            self.assertTrue((certs / "nginx-selfsigned.key").is_file())

    def test_macos_prepares_mounts_for_sudo_user(self):
        with tempfile.TemporaryDirectory() as directory:
            install_dir = Path(directory)
            (install_dir / "data").mkdir()
            (install_dir / "docker/nginx/certs").mkdir(parents=True)
            calls = install_dir / "chown-calls"
            run_installer_function(
                'PLATFORM=macos; INSTALL_DIR="$2"; LOG_FILE="$3"; '
                'SUDO_UID=501; SUDO_GID=20; '
                'CHOWN_CALLS="$4"; '
                'chown() { printf "%s\\n" "$*" >>"$CHOWN_CALLS"; }; '
                "prepare_macos_mount_permissions",
                install_dir,
                install_dir / "install.log",
                calls,
            )

            content = calls.read_text()
            self.assertIn("-R 501:20", content)
            self.assertIn(str(install_dir / "data"), content)
            self.assertIn(str(install_dir / "docker/nginx/certs"), content)

    def test_completed_macos_install_keeps_existing_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            install_dir = Path(directory)
            (install_dir / "install-info.env").write_text("complete\n")
            run_installer_function(
                'PLATFORM=macos; INSTALL_DIR="$2"; LOG_FILE="$3"; '
                'chown() { return 1; }; prepare_macos_mount_permissions',
                install_dir,
                install_dir / "install.log",
            )

    def test_linux_does_not_change_mount_ownership(self):
        with tempfile.TemporaryDirectory() as directory:
            install_dir = Path(directory)
            run_installer_function(
                'PLATFORM=linux; INSTALL_DIR="$2"; '
                'chown() { return 1; }; prepare_macos_mount_permissions',
                install_dir,
            )

    def test_fresh_install_recreates_stale_containers(self):
        with tempfile.TemporaryDirectory() as directory:
            log_file = Path(directory) / "install.log"
            run_installer_function(
                'LOG_FILE="$2"; INSTALL_DIR="$3"; EXISTING=0; '
                'run_compose_quiet() { printf "%s\\n" "$*"; }; '
                "start_stack",
                log_file,
                directory,
            )

            content = log_file.read_text()
            self.assertIn("--force-recreate", content)

    def test_existing_install_does_not_force_recreate(self):
        with tempfile.TemporaryDirectory() as directory:
            install_dir = Path(directory)
            log_file = install_dir / "install.log"
            (install_dir / "install-info.env").write_text("complete\n")
            run_installer_function(
                'LOG_FILE="$2"; INSTALL_DIR="$3"; EXISTING=1; '
                'stale_mount_namespace_detected() { return 1; }; '
                'run_compose_quiet() { printf "%s\\n" "$*"; }; '
                "start_stack",
                log_file,
                install_dir,
            )

            content = log_file.read_text()
            self.assertNotIn("--force-recreate", content)

    def test_stale_mount_namespace_forces_container_recreation(self):
        with tempfile.TemporaryDirectory() as directory:
            install_dir = Path(directory)
            log_file = install_dir / "install.log"
            (install_dir / "install-info.env").write_text("complete\n")
            result = run_installer_function(
                'LOG_FILE="$2"; INSTALL_DIR="$3"; EXISTING=1; '
                'stale_mount_namespace_detected() { return 0; }; '
                'run_compose_quiet() { printf "%s\\n" "$*"; }; '
                "start_stack",
                log_file,
                install_dir,
            )

            content = log_file.read_text()
            self.assertIn("--force-recreate", content)
            self.assertIn("stale bind-mount working directory", result.stdout)

    def test_stale_mount_namespace_error_is_detected(self):
        run_installer_function(
            'run_compose_quiet() { printf "redis-id\\n"; }; '
            'docker() { '
            'printf "current working directory is outside of container "'
            '"mount namespace root\\n"; '
            '}; stale_mount_namespace_detected'
        )

    def test_incomplete_existing_install_recreates_stale_containers(self):
        with tempfile.TemporaryDirectory() as directory:
            install_dir = Path(directory)
            log_file = install_dir / "install.log"
            run_installer_function(
                'LOG_FILE="$2"; INSTALL_DIR="$3"; EXISTING=1; '
                'run_compose_quiet() { printf "%s\\n" "$*"; }; '
                "start_stack",
                log_file,
                install_dir,
            )

            content = log_file.read_text()
            self.assertIn("--force-recreate", content)

    def test_retry_does_not_recreate_containers_again(self):
        with tempfile.TemporaryDirectory() as directory:
            install_dir = Path(directory)
            log_file = install_dir / "install.log"
            run_installer_function(
                'LOG_FILE="$2"; INSTALL_DIR="$3"; EXISTING=0; calls=0; '
                'sleep() { :; }; '
                'run_compose_quiet() { calls=$((calls + 1)); '
                'printf "%s\\n" "$*"; [[ "$calls" -gt 1 ]]; }; '
                "start_stack",
                log_file,
                install_dir,
            )

            calls = [
                line
                for line in log_file.read_text().splitlines()
                if line.startswith("up -d")
            ]
            self.assertEqual(len(calls), 2)
            self.assertIn("--force-recreate", calls[0])
            self.assertNotIn("--force-recreate", calls[1])

    def _patch_compose(self, registry):
        with tempfile.TemporaryDirectory() as directory:
            install_dir = Path(directory)
            compose = install_dir / "docker-compose.standalone.yml"
            shutil.copyfile(COMPOSE, compose)
            run_installer_function(
                'PLATFORM=linux; LOG_FILE=""; CHANNEL=cn; SOURCE_DIR=""; '
                'REGISTRY="$2"; REGISTRY_CN="$3"; VERSION=0.57.0; '
                'INSTALL_DIR="$4"; '
                "COMPOSE_FILE=docker-compose.standalone.yml; patch_compose",
                registry,
                "registry.cn-beijing.aliyuncs.com/oneprolabs",
                install_dir,
            )
            return compose.read_text()

    def test_cn_channel_pulls_infrastructure_images_from_aliyun(self):
        content = self._patch_compose(
            "registry.cn-beijing.aliyuncs.com/oneprolabs"
        )

        self.assertIn(
            "image: registry.cn-beijing.aliyuncs.com/oneprolabs/nginx:latest",
            content,
        )
        self.assertIn(
            "image: registry.cn-beijing.aliyuncs.com/oneprolabs/postgres:17",
            content,
        )
        self.assertIn(
            "image: registry.cn-beijing.aliyuncs.com/oneprolabs"
            "/redis:alpine",
            content,
        )

    def test_docker_hub_channel_keeps_bare_infrastructure_images(self):
        content = self._patch_compose("oneprolabs")

        self.assertIn("image: nginx:latest", content)
        self.assertIn("image: postgres:17", content)
        self.assertIn("image: redis:alpine", content)

    def test_unreachable_docker_hub_falls_back_to_aliyun_registry(self):
        result = run_installer_function(
            'curl() { '
            'case "$*" in *registry-1.docker.io*) printf "000" ;; '
            '*) printf "401" ;; esac; }; '
            'CHANNEL=github; DOWNLOAD_SOURCE=github; '
            'detect_download_source; printf "REG=%s\\n" "$REGISTRY"'
        )

        self.assertIn(
            "REG=registry.cn-beijing.aliyuncs.com/oneprolabs", result.stdout
        )

    def test_reachable_docker_hub_selects_docker_hub_registry(self):
        result = run_installer_function(
            'curl() { printf "401"; }; '
            'CHANNEL=github; DOWNLOAD_SOURCE=github; '
            'detect_download_source; printf "REG=%s\\n" "$REGISTRY"'
        )

        self.assertIn("REG=oneprolabs", result.stdout)

    def test_cn_channel_generates_certificate_with_host_openssl(self):
        with tempfile.TemporaryDirectory() as directory:
            install_dir = Path(directory)
            log_file = install_dir / "install.log"
            run_installer_function(
                'PLATFORM=linux; REGISTRY="$4"; REGISTRY_CN="$4"; '
                'INSTALL_DIR="$2"; DOMAIN=127.0.0.1; LOG_FILE="$3"; '
                'docker() { return 1; }; generate_certs',
                install_dir,
                log_file,
                "registry.cn-beijing.aliyuncs.com/oneprolabs",
            )

            certs = install_dir / "docker/nginx/certs"
            self.assertTrue((certs / "nginx-selfsigned.crt").is_file())
            self.assertTrue((certs / "nginx-selfsigned.key").is_file())

    def test_cn_channel_does_not_require_tls_helper_image(self):
        with tempfile.TemporaryDirectory() as directory:
            run_installer_function(
                'PLATFORM=linux; REGISTRY="$2"; REGISTRY_CN="$2"; '
                'INSTALL_DIR="$3"; ! tls_helper_image_required',
                "registry.cn-beijing.aliyuncs.com/oneprolabs",
                directory,
            )

    def test_ai_model_setup_delegates_confirmation_to_wizard(self):
        result = run_installer_function(
            'LOG_FILE=""; ASSUME_YES=0; '
            'model_setup_is_available() { return 0; }; '
            'model_is_configured() { return 1; }; '
            'model_setup_has_terminal() { return 0; }; '
            'run_model_setup_wizard() { printf "WIZARD\\n"; }; '
            "configure_ai_model"
        )

        self.assertIn("WIZARD", result.stdout)

    def test_ai_model_setup_runs_wizard_when_confirmed(self):
        result = run_installer_function(
            'LOG_FILE=""; ASSUME_YES=0; '
            'model_setup_is_available() { return 0; }; '
            'model_is_configured() { return 1; }; '
            'model_setup_has_terminal() { return 0; }; '
            'confirm() { return 0; }; '
            'run_model_setup_wizard() { printf "WIZARD\\n"; }; '
            "configure_ai_model"
        )

        self.assertIn("WIZARD", result.stdout)

    def test_ai_model_setup_skipped_with_yes_flag(self):
        result = run_installer_function(
            'LOG_FILE=""; ASSUME_YES=1; '
            'model_setup_is_available() { return 0; }; '
            'model_is_configured() { return 1; }; '
            'confirm() { printf "CONFIRM\\n"; return 0; }; '
            'run_model_setup_wizard() { printf "WIZARD\\n"; }; '
            "configure_ai_model"
        )

        self.assertNotIn("CONFIRM", result.stdout)
        self.assertNotIn("WIZARD", result.stdout)
        self.assertIn("skipped because --yes is enabled", result.stdout)


if __name__ == "__main__":
    unittest.main()
