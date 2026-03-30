"""
tests/test_sync_repo.py — lib/sync_repo.py 单元测试

覆盖范围:
- mirror_sync(): dry-run 跳过, 成功克隆+推送, 空仓库检测, 超时处理, 失败处理
  - GIT_ASKPASS 临时脚本的清理
- sync_repo_metadata(): 元信息变化检测, dry-run, 无变化跳过
- sync_labels(): 创建新标签, 更新现有标签, dry-run, URL 编码
- sync_releases(): 创建新 release, 更新现有 release, dry-run
- sync_wiki(): dry-run 跳过, clone 失败静默跳过
"""

import os
import subprocess
from unittest.mock import MagicMock, patch, call

import pytest

from lib.sync_repo import (
    mirror_sync,
    sync_labels,
    sync_releases,
    sync_repo_metadata,
    sync_wiki,
)


def _make_resp(data, status=200):
    mock = MagicMock()
    mock.status_code = status
    mock.json.return_value = data
    mock.text = str(data)
    mock.headers = {}
    return mock


def _make_process(returncode=0, stdout="", stderr=""):
    proc = MagicMock()
    proc.returncode = returncode
    proc.stdout = stdout
    proc.stderr = stderr
    return proc


# ===========================================================================
# mirror_sync
# ===========================================================================

class TestMirrorSync:
    def test_dry_run_returns_success_without_git_ops(self):
        with patch("lib.sync_repo.subprocess.run") as mock_run:
            result = mirror_sync(
                "https://github.com/src/repo.git",
                "https://gitee.com/tgt/repo.git",
                "repo",
                "src_token", "tgt_token",
                dry_run=True,
            )
        assert result == "success"
        mock_run.assert_not_called()

    def test_successful_clone_and_push(self):
        clone_proc = _make_process(returncode=0)
        push_all_proc = _make_process(returncode=0)
        push_tags_proc = _make_process(returncode=0)

        with patch("lib.sync_repo.subprocess.run",
                   side_effect=[clone_proc, push_all_proc, push_tags_proc]), \
             patch("lib.sync_repo.make_git_env",
                   return_value=({}, "/tmp/fake_askpass")) as mock_env, \
             patch("lib.sync_repo.shutil.rmtree"), \
             patch("lib.sync_repo.os.unlink"), \
             patch("lib.sync_repo.tempfile.mkdtemp", return_value="/tmp/testdir"):
            result = mirror_sync(
                "https://github.com/src/repo.git",
                "https://gitee.com/tgt/repo.git",
                "repo",
                "src_token", "tgt_token",
            )
        assert result == "success"
        assert mock_env.call_count == 2  # once for clone, once for push

    def test_detects_empty_repo_from_stderr(self):
        clone_proc = _make_process(returncode=0, stderr="warning: You appear to have cloned an empty repository")
        # push not reached for empty repo
        with patch("lib.sync_repo.subprocess.run",
                   return_value=clone_proc), \
             patch("lib.sync_repo.make_git_env",
                   return_value=({}, "/tmp/fake")), \
             patch("lib.sync_repo.shutil.rmtree"), \
             patch("lib.sync_repo.os.unlink"), \
             patch("lib.sync_repo.tempfile.mkdtemp", return_value="/tmp/testdir"):
            result = mirror_sync(
                "https://github.com/src/repo.git",
                "https://gitee.com/tgt/repo.git",
                "repo",
                "src_token", "tgt_token",
            )
        assert result == "empty"

    def test_clone_failure_returns_failed(self):
        clone_proc = _make_process(returncode=1, stderr="fatal: repository not found")
        with patch("lib.sync_repo.subprocess.run",
                   return_value=clone_proc), \
             patch("lib.sync_repo.make_git_env",
                   return_value=({}, "/tmp/fake")), \
             patch("lib.sync_repo.shutil.rmtree"), \
             patch("lib.sync_repo.os.unlink"), \
             patch("lib.sync_repo.tempfile.mkdtemp", return_value="/tmp/testdir"):
            result = mirror_sync(
                "https://github.com/src/repo.git",
                "https://gitee.com/tgt/repo.git",
                "repo",
                "src_token", "tgt_token",
            )
        assert result == "failed"

    def test_push_failure_returns_failed(self):
        clone_proc = _make_process(returncode=0)
        push_all_proc = _make_process(returncode=1, stderr="error: push failed")
        with patch("lib.sync_repo.subprocess.run",
                   side_effect=[clone_proc, push_all_proc]), \
             patch("lib.sync_repo.make_git_env",
                   return_value=({}, "/tmp/fake")), \
             patch("lib.sync_repo.shutil.rmtree"), \
             patch("lib.sync_repo.os.unlink"), \
             patch("lib.sync_repo.tempfile.mkdtemp", return_value="/tmp/testdir"):
            result = mirror_sync(
                "https://github.com/src/repo.git",
                "https://gitee.com/tgt/repo.git",
                "repo",
                "src_token", "tgt_token",
            )
        assert result == "failed"

    def test_tags_push_failure_returns_failed(self):
        clone_proc = _make_process(returncode=0)
        push_all_proc = _make_process(returncode=0)
        push_tags_proc = _make_process(returncode=1, stderr="error: tags push failed")
        with patch("lib.sync_repo.subprocess.run",
                   side_effect=[clone_proc, push_all_proc, push_tags_proc]), \
             patch("lib.sync_repo.make_git_env",
                   return_value=({}, "/tmp/fake")), \
             patch("lib.sync_repo.shutil.rmtree"), \
             patch("lib.sync_repo.os.unlink"), \
             patch("lib.sync_repo.tempfile.mkdtemp", return_value="/tmp/testdir"):
            result = mirror_sync(
                "https://github.com/src/repo.git",
                "https://gitee.com/tgt/repo.git",
                "repo",
                "src_token", "tgt_token",
            )
        assert result == "failed"

    def test_timeout_returns_failed(self):
        with patch("lib.sync_repo.subprocess.run",
                   side_effect=subprocess.TimeoutExpired("git", 600)), \
             patch("lib.sync_repo.make_git_env",
                   return_value=({}, "/tmp/fake")), \
             patch("lib.sync_repo.shutil.rmtree"), \
             patch("lib.sync_repo.os.unlink"), \
             patch("lib.sync_repo.tempfile.mkdtemp", return_value="/tmp/testdir"):
            result = mirror_sync(
                "https://github.com/src/repo.git",
                "https://gitee.com/tgt/repo.git",
                "repo",
                "src_token", "tgt_token",
            )
        assert result == "failed"

    def test_askpass_scripts_cleaned_up_on_success(self):
        clone_proc = _make_process(returncode=0)
        push_all_proc = _make_process(returncode=0)
        push_tags_proc = _make_process(returncode=0)
        askpass_paths = ["/tmp/askpass_clone.sh", "/tmp/askpass_push.sh"]
        call_count = [0]

        def fake_make_git_env(token, username="git"):
            p = askpass_paths[call_count[0]]
            call_count[0] += 1
            return {}, p

        unlinked = []
        opened_files = []

        def track_open(path, mode):
            opened_files.append(path)
            # Return a mock file object that supports write and context manager
            from io import BytesIO
            return BytesIO()

        with patch("lib.sync_repo.subprocess.run",
                   side_effect=[clone_proc, push_all_proc, push_tags_proc]), \
             patch("lib.sync_repo.make_git_env",
                   side_effect=fake_make_git_env), \
             patch("lib.sync_repo.shutil.rmtree"), \
             patch("lib.sync_repo.os.path.exists", return_value=True), \
             patch("lib.sync_repo.os.path.getsize", return_value=100), \
             patch("builtins.open", side_effect=track_open), \
             patch("lib.sync_repo.os.unlink", side_effect=unlinked.append), \
             patch("lib.sync_repo.tempfile.mkdtemp", return_value="/tmp/testdir"):
            mirror_sync(
                "https://github.com/src/repo.git",
                "https://gitee.com/tgt/repo.git",
                "repo",
                "src_token", "tgt_token",
            )
        assert "/tmp/askpass_clone.sh" in unlinked
        assert "/tmp/askpass_push.sh" in unlinked
        # Verify that files were opened for zero-overwriting
        assert "/tmp/askpass_clone.sh" in opened_files
        assert "/tmp/askpass_push.sh" in opened_files

    def test_temp_dir_always_cleaned_up(self):
        clone_proc = _make_process(returncode=1, stderr="fatal error")
        rmtree_calls = []
        with patch("lib.sync_repo.subprocess.run",
                   return_value=clone_proc), \
             patch("lib.sync_repo.make_git_env",
                   return_value=({}, "/tmp/fake")), \
             patch("lib.sync_repo.shutil.rmtree",
                   side_effect=lambda path, **kw: rmtree_calls.append(path)), \
             patch("lib.sync_repo.os.unlink"), \
             patch("lib.sync_repo.tempfile.mkdtemp", return_value="/tmp/testdir"):
            mirror_sync(
                "https://github.com/src/repo.git",
                "https://gitee.com/tgt/repo.git",
                "repo",
                "src_token", "tgt_token",
            )
        assert "/tmp/testdir" in rmtree_calls


# ===========================================================================
# sync_wiki
# ===========================================================================

class TestSyncWiki:
    def test_dry_run_skips_git_ops(self):
        with patch("lib.sync_repo.subprocess.run") as mock_run:
            sync_wiki("github", "gitee", "src_owner", "tgt_owner",
                      "src_token", "tgt_token", "repo", dry_run=True)
        mock_run.assert_not_called()

    def test_warns_when_clone_fails(self):
        """Wiki 不存在时（clone 失败）输出 warning 并跳过"""
        clone_proc = _make_process(returncode=128, stderr="not found")
        with patch("lib.sync_repo.subprocess.run",
                   return_value=clone_proc), \
             patch("lib.sync_repo.make_git_env",
                   return_value=({}, "/tmp/fake")), \
             patch("lib.sync_repo.shutil.rmtree"), \
             patch("lib.sync_repo.os.unlink"), \
             patch("lib.sync_repo.tempfile.mkdtemp", return_value="/tmp/wiki"), \
             patch("lib.sync_repo.logging.warning") as mock_warn:
            sync_wiki("github", "gitee", "src_owner", "tgt_owner",
                      "src_token", "tgt_token", "repo")
        assert any("Wiki not available" in str(c) for c in mock_warn.call_args_list)

    def test_builds_wiki_url_correctly(self):
        clone_proc = _make_process(returncode=0)
        push_proc = _make_process(returncode=0)
        run_calls = []

        def fake_run(args, **kwargs):
            run_calls.append(args)
            return _make_process(returncode=0)

        with patch("lib.sync_repo.subprocess.run", side_effect=fake_run), \
             patch("lib.sync_repo.make_git_env",
                   return_value=({}, "/tmp/fake")), \
             patch("lib.sync_repo.shutil.rmtree"), \
             patch("lib.sync_repo.os.unlink"), \
             patch("lib.sync_repo.tempfile.mkdtemp", return_value="/tmp/wiki"):
            sync_wiki("github", "gitee", "src_owner", "tgt_owner",
                      "src_token", "tgt_token", "myrepo")

        clone_cmd = run_calls[0]
        assert "myrepo.wiki.git" in clone_cmd[-2]
        assert "@" not in clone_cmd[-2]  # No token in URL


# ===========================================================================
# sync_repo_metadata
# ===========================================================================

class TestSyncRepoMetadata:
    def test_dry_run_skips_update_when_diff_detected(self):
        src_details = {"description": "New desc", "homepage": ""}
        tgt_details = {"description": "Old desc", "homepage": ""}

        with patch("lib.sync_repo.get_github_repo_details",
                   return_value=src_details), \
             patch("lib.sync_repo.get_gitee_repo_details",
                   return_value=tgt_details), \
             patch("lib.sync_repo.update_gitee_repo_metadata") as mock_update:
            sync_repo_metadata("github", "gitee", "src", "tgt",
                               "tok1", "tok2", "repo", dry_run=True)
        mock_update.assert_not_called()

    def test_no_update_when_metadata_same(self):
        details = {"description": "Same desc", "homepage": "https://example.com"}
        with patch("lib.sync_repo.get_github_repo_details",
                   return_value=details), \
             patch("lib.sync_repo.get_gitee_repo_details",
                   return_value=details), \
             patch("lib.sync_repo.update_gitee_repo_metadata") as mock_update:
            sync_repo_metadata("github", "gitee", "src", "tgt",
                               "tok1", "tok2", "repo")
        mock_update.assert_not_called()

    def test_updates_when_description_differs(self):
        src = {"description": "New desc", "homepage": ""}
        tgt = {"description": "Old desc", "homepage": ""}
        with patch("lib.sync_repo.get_github_repo_details", return_value=src), \
             patch("lib.sync_repo.get_gitee_repo_details", return_value=tgt), \
             patch("lib.sync_repo.update_gitee_repo_metadata") as mock_update:
            sync_repo_metadata("github", "gitee", "src", "tgt",
                               "tok1", "tok2", "repo")
        mock_update.assert_called_once()
        _, kwargs = mock_update.call_args
        # Called positionally: (owner, token, repo_name, updates)
        args = mock_update.call_args[0]
        assert "description" in args[3]

    def test_handles_source_fetch_failure_gracefully(self):
        with patch("lib.sync_repo.get_github_repo_details", return_value=None):
            # Should not raise
            sync_repo_metadata("github", "gitee", "src", "tgt",
                               "tok1", "tok2", "repo")


# ===========================================================================
# sync_labels
# ===========================================================================

class TestSyncLabels:
    def _label(self, name, color="ff0000", description=""):
        return {"name": name, "color": color, "description": description}

    def test_dry_run_skips_create(self):
        src_labels = [self._label("bug")]
        tgt_labels = []
        with patch("lib.sync_repo.paginated_get",
                   side_effect=[src_labels, tgt_labels]), \
             patch("lib.sync_repo.api_request") as mock_api:
            sync_labels("github", "gitee", "src", "tgt",
                        "tok1", "tok2", "repo", dry_run=True)
        mock_api.assert_not_called()

    def test_dry_run_skips_update(self):
        src_labels = [self._label("bug", color="ff0000", description="new desc")]
        tgt_labels = [self._label("bug", color="ff0000", description="old desc")]
        with patch("lib.sync_repo.paginated_get",
                   side_effect=[src_labels, tgt_labels]), \
             patch("lib.sync_repo.api_request") as mock_api:
            sync_labels("github", "gitee", "src", "tgt",
                        "tok1", "tok2", "repo", dry_run=True)
        mock_api.assert_not_called()

    def test_creates_new_label_on_target(self):
        src_labels = [self._label("enhancement")]
        tgt_labels = []
        mock_resp = _make_resp({}, status=201)
        with patch("lib.sync_repo.paginated_get",
                   side_effect=[src_labels, tgt_labels]), \
             patch("lib.sync_repo.api_request",
                   return_value=mock_resp) as mock_api:
            sync_labels("github", "gitee", "src", "tgt",
                        "tok1", "tok2", "repo")
        mock_api.assert_called_once()
        method, url = mock_api.call_args[0]
        assert method == "POST"

    def test_updates_label_when_color_differs(self):
        src_labels = [self._label("bug", color="ff0000")]
        tgt_labels = [self._label("bug", color="0000ff")]
        mock_resp = _make_resp({}, status=200)
        with patch("lib.sync_repo.paginated_get",
                   side_effect=[src_labels, tgt_labels]), \
             patch("lib.sync_repo.api_request",
                   return_value=mock_resp) as mock_api:
            sync_labels("github", "gitee", "src", "tgt",
                        "tok1", "tok2", "repo")
        mock_api.assert_called_once()
        method, url = mock_api.call_args[0]
        assert method == "PATCH"

    def test_no_update_when_label_unchanged(self):
        src_labels = [self._label("bug", color="ff0000", description="A bug")]
        tgt_labels = [self._label("bug", color="ff0000", description="A bug")]
        with patch("lib.sync_repo.paginated_get",
                   side_effect=[src_labels, tgt_labels]), \
             patch("lib.sync_repo.api_request") as mock_api:
            sync_labels("github", "gitee", "src", "tgt",
                        "tok1", "tok2", "repo")
        mock_api.assert_not_called()

    def test_url_encodes_label_name_with_spaces(self):
        src_labels = [self._label("good first issue", color="7057ff")]
        tgt_labels = [self._label("good first issue", color="000000")]  # different
        mock_resp = _make_resp({}, status=200)
        with patch("lib.sync_repo.paginated_get",
                   side_effect=[src_labels, tgt_labels]), \
             patch("lib.sync_repo.api_request",
                   return_value=mock_resp) as mock_api:
            sync_labels("github", "gitee", "src", "tgt",
                        "tok1", "tok2", "repo")
        method, url = mock_api.call_args[0]
        assert method == "PATCH"
        assert " " not in url, "URL must not contain unencoded spaces"
        assert "good%20first%20issue" in url or "good+first+issue" in url

    def test_sends_description_even_when_empty_for_clearing(self):
        """始终发送 description 字段，即使为空（允许清除描述）"""
        src_labels = [self._label("bug", color="ff0000", description="")]
        tgt_labels = [self._label("bug", color="ff0000", description="old desc")]
        mock_resp = _make_resp({}, status=200)
        with patch("lib.sync_repo.paginated_get",
                   side_effect=[src_labels, tgt_labels]), \
             patch("lib.sync_repo.api_request",
                   return_value=mock_resp) as mock_api:
            sync_labels("github", "gitee", "src", "tgt",
                        "tok1", "tok2", "repo")
        mock_api.assert_called_once()
        _, kwargs = mock_api.call_args
        assert "description" in kwargs["json"]
        assert kwargs["json"]["description"] == ""


# ===========================================================================
# sync_releases
# ===========================================================================

class TestSyncReleases:
    def _release(self, tag, name=None, body="", prerelease=False, assets=None):
        return {
            "tag_name": tag,
            "name": name or tag,
            "body": body,
            "prerelease": prerelease,
            "draft": False,
            "id": hash(tag),
            "assets": assets or [],
            "upload_url": "https://uploads.github.com/repos/o/r/releases/1/assets{?name,label}",
        }

    def test_dry_run_skips_create(self):
        src_releases = [self._release("v1.0.0")]
        tgt_releases = []
        with patch("lib.sync_repo.paginated_get",
                   side_effect=[src_releases, tgt_releases]), \
             patch("lib.sync_repo.api_request") as mock_api:
            sync_releases("github", "gitee", "src", "tgt",
                          "tok1", "tok2", "repo", dry_run=True)
        mock_api.assert_not_called()

    def test_creates_new_release(self):
        src_releases = [self._release("v1.0.0")]
        tgt_releases = []
        mock_resp = _make_resp(self._release("v1.0.0"), status=201)
        with patch("lib.sync_repo.paginated_get",
                   side_effect=[src_releases, tgt_releases]), \
             patch("lib.sync_repo.api_request",
                   return_value=mock_resp) as mock_api:
            sync_releases("github", "gitee", "src", "tgt",
                          "tok1", "tok2", "repo")
        mock_api.assert_called_once()
        method, url = mock_api.call_args[0]
        assert method == "POST"

    def test_updates_existing_release_when_body_differs(self):
        src_releases = [self._release("v1.0.0", body="new release notes")]
        tgt_releases = [self._release("v1.0.0", body="old release notes")]
        mock_resp = _make_resp({}, status=200)
        with patch("lib.sync_repo.paginated_get",
                   side_effect=[src_releases, tgt_releases]), \
             patch("lib.sync_repo.api_request",
                   return_value=mock_resp) as mock_api:
            sync_releases("github", "gitee", "src", "tgt",
                          "tok1", "tok2", "repo")
        mock_api.assert_called()
        method, url = mock_api.call_args[0]
        assert method == "PATCH"

    def test_skips_release_without_tag(self):
        src_releases = [{"tag_name": None, "name": "no-tag"}]
        tgt_releases = []
        with patch("lib.sync_repo.paginated_get",
                   side_effect=[src_releases, tgt_releases]), \
             patch("lib.sync_repo.api_request") as mock_api:
            sync_releases("github", "gitee", "src", "tgt",
                          "tok1", "tok2", "repo")
        mock_api.assert_not_called()


# ===========================================================================
# log_repo_name masking — 验证 log_repo_name 在各函数中正确脱敏
# ===========================================================================

class TestLogRepoNameMasking:
    """验证 log_repo_name 参数能防止私有仓库名泄露到日志中。"""

    def test_mirror_sync_dry_run_uses_log_repo_name(self):
        """mirror_sync dry-run 应使用 log_repo_name 而非 repo_name"""
        with patch("lib.sync_repo.subprocess.run") as mock_run, \
             patch("lib.sync_repo.logging.info") as mock_log:
            mirror_sync(
                "https://github.com/src/secret.git",
                "https://gitee.com/tgt/secret.git",
                "secret-repo",
                "src_token", "tgt_token",
                dry_run=True,
                log_repo_name="[private]",
            )
        log_messages = " ".join(str(c) for c in mock_log.call_args_list)
        assert "[private]" in log_messages
        assert "secret-repo" not in log_messages

    def test_mirror_sync_empty_repo_warning_uses_log_repo_name(self):
        """空仓库 WARNING 应使用 log_repo_name 而非 repo_name"""
        clone_proc = _make_process(
            returncode=0,
            stderr="warning: You appear to have cloned an empty repository",
        )
        with patch("lib.sync_repo.subprocess.run",
                   return_value=clone_proc), \
             patch("lib.sync_repo.make_git_env",
                   return_value=({}, "/tmp/fake")), \
             patch("lib.sync_repo.shutil.rmtree"), \
             patch("lib.sync_repo.os.unlink"), \
             patch("lib.sync_repo.tempfile.mkdtemp",
                   return_value="/tmp/testdir"), \
             patch("lib.sync_repo.logging.warning") as mock_warn:
            result = mirror_sync(
                "https://github.com/src/secret.git",
                "https://gitee.com/tgt/secret.git",
                "secret-repo",
                "src_token", "tgt_token",
                log_repo_name="[private]",
            )
        assert result == "empty"
        warn_messages = " ".join(str(c) for c in mock_warn.call_args_list)
        assert "[private]" in warn_messages
        assert "secret-repo" not in warn_messages

    def test_mirror_sync_defaults_log_repo_name_to_repo_name(self):
        """log_repo_name 未指定时默认使用 repo_name"""
        with patch("lib.sync_repo.subprocess.run") as mock_run, \
             patch("lib.sync_repo.logging.info") as mock_log:
            mirror_sync(
                "https://github.com/src/repo.git",
                "https://gitee.com/tgt/repo.git",
                "my-repo",
                "src_token", "tgt_token",
                dry_run=True,
            )
        log_messages = " ".join(str(c) for c in mock_log.call_args_list)
        assert "my-repo" in log_messages

    def test_sync_wiki_dry_run_uses_log_repo_name(self):
        """sync_wiki dry-run 应使用 log_repo_name 而非 repo_name"""
        with patch("lib.sync_repo.subprocess.run") as mock_run, \
             patch("lib.sync_repo.logging.info") as mock_log:
            sync_wiki("github", "gitee", "src_owner", "tgt_owner",
                      "src_token", "tgt_token", "secret-repo",
                      dry_run=True, log_repo_name="[private]")
        log_messages = " ".join(str(c) for c in mock_log.call_args_list)
        assert "[private]" in log_messages
        assert "secret-repo" not in log_messages

    def test_sync_wiki_clone_failure_warning_uses_log_repo_name(self):
        """Wiki clone 失败 WARNING 应使用 log_repo_name 而非 repo_name"""
        clone_proc = _make_process(returncode=128, stderr="not found")
        with patch("lib.sync_repo.subprocess.run",
                   return_value=clone_proc), \
             patch("lib.sync_repo.make_git_env",
                   return_value=({}, "/tmp/fake")), \
             patch("lib.sync_repo.shutil.rmtree"), \
             patch("lib.sync_repo.os.unlink"), \
             patch("lib.sync_repo.tempfile.mkdtemp",
                   return_value="/tmp/wiki"), \
             patch("lib.sync_repo.logging.warning") as mock_warn:
            sync_wiki("github", "gitee", "src_owner", "tgt_owner",
                      "src_token", "tgt_token", "secret-repo",
                      log_repo_name="[private]")
        warn_messages = " ".join(str(c) for c in mock_warn.call_args_list)
        assert "[private]" in warn_messages
        assert "secret-repo" not in warn_messages
