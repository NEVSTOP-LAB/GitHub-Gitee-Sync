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

import logging
import os
import subprocess
from unittest.mock import MagicMock, patch, call

import pytest

from lib.sync_repo import (
    _refs_already_in_sync,
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
             patch("lib.sync_repo._refs_already_in_sync", return_value=False), \
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
             patch("lib.sync_repo._refs_already_in_sync", return_value=False), \
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
             patch("lib.sync_repo._refs_already_in_sync", return_value=False), \
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

    def test_timeout_retries_once(self):
        """On timeout mirror_sync retries once, logging a warning then an error."""
        import lib.sync_repo as sr
        orig_retries = sr.GIT_RETRIES
        sr.GIT_RETRIES = 1
        try:
            warnings = []
            errors = []
            with patch("lib.sync_repo.subprocess.run",
                       side_effect=subprocess.TimeoutExpired("git", 1800)), \
                 patch("lib.sync_repo.make_git_env",
                       return_value=({}, "/tmp/fake")), \
                 patch("lib.sync_repo.shutil.rmtree"), \
                 patch("lib.sync_repo.os.unlink"), \
                 patch("lib.sync_repo.tempfile.mkdtemp", return_value="/tmp/testdir"), \
                 patch("lib.sync_repo.logging.warning",
                       side_effect=lambda msg, *a, **kw: warnings.append(str(msg))), \
                 patch("lib.sync_repo.logging.error",
                       side_effect=lambda msg, *a, **kw: errors.append(str(msg))):
                result = mirror_sync(
                    "https://github.com/src/repo.git",
                    "https://gitee.com/tgt/repo.git",
                    "repo",
                    "src_token", "tgt_token",
                )
            assert result == "failed"
            # Exactly one warning (retry message) and one error (final failure)
            assert any("retrying" in w.lower() for w in warnings)
            assert any("timed out" in e.lower() for e in errors)
        finally:
            sr.GIT_RETRIES = orig_retries

    def test_timeout_succeeds_on_retry(self):
        """If the first attempt times out but the second succeeds, returns success."""
        import lib.sync_repo as sr
        orig_retries = sr.GIT_RETRIES
        sr.GIT_RETRIES = 1
        try:
            clone_ok = _make_process(returncode=0, stdout="", stderr="")
            push_all_ok = _make_process(returncode=0)
            push_tags_ok = _make_process(returncode=0)
            # First call (clone, attempt 0) times out; subsequent calls succeed.
            # _refs_already_in_sync is patched to return False so we don't need
            # to supply additional subprocess.run entries for show-ref/ls-remote.
            with patch("lib.sync_repo.subprocess.run",
                       side_effect=[
                           subprocess.TimeoutExpired("git", 1800),
                           clone_ok, push_all_ok, push_tags_ok,
                       ]), \
                 patch("lib.sync_repo._refs_already_in_sync", return_value=False), \
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
            assert result == "success"
        finally:
            sr.GIT_RETRIES = orig_retries

    def test_git_timeout_parameter_passed_to_subprocess(self):
        """git_timeout parameter is used in subprocess calls."""
        clone_proc = _make_process(returncode=0)
        push_all_proc = _make_process(returncode=0)
        push_tags_proc = _make_process(returncode=0)
        # _refs_already_in_sync is patched to return False so we don't need to
        # supply additional subprocess.run entries for show-ref/ls-remote.
        with patch("lib.sync_repo.subprocess.run",
                   side_effect=[clone_proc, push_all_proc, push_tags_proc]) as mock_run, \
             patch("lib.sync_repo._refs_already_in_sync", return_value=False), \
             patch("lib.sync_repo.make_git_env",
                   return_value=({}, "/tmp/fake")), \
             patch("lib.sync_repo.shutil.rmtree"), \
             patch("lib.sync_repo.os.unlink"), \
             patch("lib.sync_repo.tempfile.mkdtemp", return_value="/tmp/testdir"):
            mirror_sync(
                "https://github.com/src/repo.git",
                "https://gitee.com/tgt/repo.git",
                "repo",
                "src_token", "tgt_token",
                git_timeout=1200,
            )
        # At least one subprocess.run call should use timeout=1200
        timeout_values = [
            call.kwargs.get("timeout") for call in mock_run.call_args_list
            if "timeout" in call.kwargs
        ]
        assert 1200 in timeout_values

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
             patch("lib.sync_repo._refs_already_in_sync", return_value=False), \
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
# _refs_already_in_sync
# ===========================================================================

class TestRefsAlreadyInSync:
    """Tests for the _refs_already_in_sync() helper."""

    _SHOW_REF_OUTPUT = (
        "abc123 refs/heads/main\n"
        "def456 refs/tags/v1.0\n"
    )
    _LS_REMOTE_IN_SYNC = (
        "abc123\trefs/heads/main\n"
        "def456\trefs/tags/v1.0\n"
    )

    def _run_side_effects(self, show_ref_out, ls_remote_rc, ls_remote_out):
        """Build side_effect list: [show-ref result, ls-remote result]."""
        return [
            _make_process(returncode=0, stdout=show_ref_out),
            _make_process(returncode=ls_remote_rc, stdout=ls_remote_out),
        ]

    def test_returns_true_when_refs_match(self):
        with patch("lib.sync_repo.subprocess.run",
                   side_effect=self._run_side_effects(
                       self._SHOW_REF_OUTPUT, 0, self._LS_REMOTE_IN_SYNC)):
            assert _refs_already_in_sync("/tmp/repo", "https://target.git", {}) is True

    def test_returns_false_when_branch_hash_differs(self):
        ls_out = "999999\trefs/heads/main\ndef456\trefs/tags/v1.0\n"
        with patch("lib.sync_repo.subprocess.run",
                   side_effect=self._run_side_effects(
                       self._SHOW_REF_OUTPUT, 0, ls_out)):
            assert _refs_already_in_sync("/tmp/repo", "https://target.git", {}) is False

    def test_returns_false_when_target_missing_branch(self):
        ls_out = "def456\trefs/tags/v1.0\n"
        with patch("lib.sync_repo.subprocess.run",
                   side_effect=self._run_side_effects(
                       self._SHOW_REF_OUTPUT, 0, ls_out)):
            assert _refs_already_in_sync("/tmp/repo", "https://target.git", {}) is False

    def test_returns_false_when_show_ref_fails(self):
        with patch("lib.sync_repo.subprocess.run",
                   return_value=_make_process(returncode=1)):
            assert _refs_already_in_sync("/tmp/repo", "https://target.git", {}) is False

    def test_returns_false_when_ls_remote_fails(self):
        with patch("lib.sync_repo.subprocess.run",
                   side_effect=self._run_side_effects(
                       self._SHOW_REF_OUTPUT, 128, "")):
            assert _refs_already_in_sync("/tmp/repo", "https://target.git", {}) is False

    def test_returns_false_on_timeout(self):
        with patch("lib.sync_repo.subprocess.run",
                   side_effect=[
                       _make_process(returncode=0, stdout=self._SHOW_REF_OUTPUT),
                       subprocess.TimeoutExpired("git", 600),
                   ]):
            assert _refs_already_in_sync("/tmp/repo", "https://target.git", {}) is False

    def test_ignores_peeled_annotated_tag_refs(self):
        """^{} peeled refs from ls-remote should be ignored in comparison."""
        ls_out = (
            "abc123\trefs/heads/main\n"
            "def456\trefs/tags/v1.0\n"
            "789012\trefs/tags/v1.0^{}\n"  # peeled — should be ignored
        )
        with patch("lib.sync_repo.subprocess.run",
                   side_effect=self._run_side_effects(
                       self._SHOW_REF_OUTPUT, 0, ls_out)):
            assert _refs_already_in_sync("/tmp/repo", "https://target.git", {}) is True

    def test_ignores_extra_refs_only_in_target(self):
        """Extra branches/tags in target that are absent in source are allowed."""
        ls_out = (
            "abc123\trefs/heads/main\n"
            "def456\trefs/tags/v1.0\n"
            "111111\trefs/heads/feature-only-on-target\n"  # extra — not in source
        )
        with patch("lib.sync_repo.subprocess.run",
                   side_effect=self._run_side_effects(
                       self._SHOW_REF_OUTPUT, 0, ls_out)):
            # Target has an extra branch, but all SOURCE branches/tags match → skip
            assert _refs_already_in_sync("/tmp/repo", "https://target.git", {}) is True


class TestMirrorSyncSkipWhenInSync:
    """Tests for the push-skip optimization in mirror_sync()."""

    def test_skips_push_and_logs_when_already_in_sync(self, caplog):
        """When both sides are in sync, push should be skipped."""
        clone_proc = _make_process(returncode=0)

        show_ref_out = "abc123 refs/heads/main\n"
        ls_remote_out = "abc123\trefs/heads/main\n"
        in_sync_procs = [
            _make_process(returncode=0, stdout=show_ref_out),
            _make_process(returncode=0, stdout=ls_remote_out),
        ]

        run_calls = []

        def fake_run(args, **kwargs):
            run_calls.append(args)
            if args[1] == "clone":
                return clone_proc
            return in_sync_procs.pop(0)

        with patch("lib.sync_repo.subprocess.run", side_effect=fake_run), \
             patch("lib.sync_repo.make_git_env",
                   return_value=({}, "/tmp/fake_askpass")), \
             patch("lib.sync_repo.shutil.rmtree"), \
             patch("lib.sync_repo.os.unlink"), \
             patch("lib.sync_repo.tempfile.mkdtemp", return_value="/tmp/testdir"), \
             caplog.at_level(logging.INFO, logger="root"):
            result = mirror_sync(
                "https://github.com/src/repo.git",
                "https://gitee.com/tgt/repo.git",
                "repo",
                "src_token", "tgt_token",
            )

        assert result == "success"
        # No "git push" command should have been run
        push_calls = [c for c in run_calls if "push" in c]
        assert push_calls == [], f"Unexpected push calls: {push_calls}"
        assert any("already in sync" in r.message for r in caplog.records)

    def test_pushes_when_refs_differ(self):
        """When refs differ, push should still be executed."""
        clone_proc = _make_process(returncode=0)

        show_ref_out = "abc123 refs/heads/main\n"
        ls_remote_out = "999999\trefs/heads/main\n"  # different hash

        run_calls = []

        def fake_run(args, **kwargs):
            run_calls.append(args)
            cmd = args[1] if len(args) > 1 else args[0]
            if cmd == "clone":
                return clone_proc
            elif cmd == "show-ref":
                return _make_process(returncode=0, stdout=show_ref_out)
            elif cmd == "ls-remote":
                return _make_process(returncode=0, stdout=ls_remote_out)
            # push --all and push --tags
            return _make_process(returncode=0)

        with patch("lib.sync_repo.subprocess.run", side_effect=fake_run), \
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

        assert result == "success"
        push_calls = [c for c in run_calls if len(c) > 1 and c[1] == "push"]
        assert len(push_calls) == 2  # one for --all and one for --tags




class TestSyncWiki:
    def test_dry_run_skips_git_ops(self):
        with patch("lib.sync_repo.subprocess.run") as mock_run:
            sync_wiki("github", "gitee", "src_owner", "tgt_owner",
                      "src_token", "tgt_token", "repo", dry_run=True)
        mock_run.assert_not_called()

    def test_logs_info_when_clone_fails(self):
        """Wiki 不存在时（clone 失败）输出 info 并跳过"""
        clone_proc = _make_process(returncode=128, stderr="not found")
        with patch("lib.sync_repo.subprocess.run",
                   return_value=clone_proc), \
             patch("lib.sync_repo.make_git_env",
                   return_value=({}, "/tmp/fake")), \
             patch("lib.sync_repo.shutil.rmtree"), \
             patch("lib.sync_repo.os.unlink"), \
             patch("lib.sync_repo.tempfile.mkdtemp", return_value="/tmp/wiki"), \
             patch("lib.sync_repo.logging.info") as mock_info:
            sync_wiki("github", "gitee", "src_owner", "tgt_owner",
                      "src_token", "tgt_token", "repo")
        assert any("Wiki not available" in str(c) for c in mock_info.call_args_list)

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

    def test_skips_push_when_wiki_already_in_sync(self, caplog):
        """Wiki push should be skipped when both sides already match."""
        show_ref_out = "abc123 refs/heads/master\n"
        ls_remote_out = "abc123\trefs/heads/master\n"
        in_sync_procs = [
            # 1st: target wiki existence check (ls-remote)
            _make_process(returncode=0, stdout=ls_remote_out),
            # 2nd: _refs_already_in_sync → show-ref
            _make_process(returncode=0, stdout=show_ref_out),
            # 3rd: _refs_already_in_sync → ls-remote
            _make_process(returncode=0, stdout=ls_remote_out),
        ]
        run_calls = []

        def fake_run(args, **kwargs):
            run_calls.append(args)
            if args[1] == "clone":
                return _make_process(returncode=0)
            return in_sync_procs.pop(0)

        with patch("lib.sync_repo.subprocess.run", side_effect=fake_run), \
             patch("lib.sync_repo.make_git_env",
                   return_value=({}, "/tmp/fake")), \
             patch("lib.sync_repo.shutil.rmtree"), \
             patch("lib.sync_repo.os.unlink"), \
             patch("lib.sync_repo.tempfile.mkdtemp", return_value="/tmp/wiki"), \
             caplog.at_level(logging.INFO, logger="root"):
            sync_wiki("github", "gitee", "src_owner", "tgt_owner",
                      "src_token", "tgt_token", "repo")

        push_calls = [c for c in run_calls if len(c) > 1 and c[1] == "push"]
        assert push_calls == [], f"Unexpected wiki push calls: {push_calls}"
        assert any("already in sync" in r.message for r in caplog.records)

    def test_pushes_wiki_when_refs_differ(self):
        """Wiki push should run when refs differ."""
        show_ref_out = "abc123 refs/heads/master\n"
        ls_remote_out = "999999\trefs/heads/master\n"  # different hash

        run_calls = []

        def fake_run(args, **kwargs):
            run_calls.append(args)
            cmd = args[1] if len(args) > 1 else args[0]
            if cmd == "clone":
                return _make_process(returncode=0)
            elif cmd == "show-ref":
                return _make_process(returncode=0, stdout=show_ref_out)
            elif cmd == "ls-remote":
                return _make_process(returncode=0, stdout=ls_remote_out)
            return _make_process(returncode=0)

        with patch("lib.sync_repo.subprocess.run", side_effect=fake_run), \
             patch("lib.sync_repo.make_git_env",
                   return_value=({}, "/tmp/fake")), \
             patch("lib.sync_repo.shutil.rmtree"), \
             patch("lib.sync_repo.os.unlink"), \
             patch("lib.sync_repo.tempfile.mkdtemp", return_value="/tmp/wiki"):
            sync_wiki("github", "gitee", "src_owner", "tgt_owner",
                      "src_token", "tgt_token", "repo")

        push_calls = [c for c in run_calls if len(c) > 1 and c[1] == "push"]
        assert len(push_calls) >= 1  # at least --all push was executed

    def test_skips_push_when_target_wiki_not_available(self, caplog):
        """Wiki push should be skipped when target wiki does not exist (404)."""
        run_calls = []

        def fake_run(args, **kwargs):
            run_calls.append(args)
            cmd = args[1] if len(args) > 1 else args[0]
            if cmd == "clone":
                return _make_process(returncode=0)
            elif cmd == "ls-remote":
                # Target wiki does not exist → ls-remote fails with "not found"
                return _make_process(
                    returncode=128,
                    stderr="remote: [session-abc] 404 not found!\n"
                           "fatal: repository not found",
                )
            return _make_process(returncode=0)

        with patch("lib.sync_repo.subprocess.run", side_effect=fake_run), \
             patch("lib.sync_repo.make_git_env",
                   return_value=({}, "/tmp/fake")), \
             patch("lib.sync_repo.shutil.rmtree"), \
             patch("lib.sync_repo.os.unlink"), \
             patch("lib.sync_repo.tempfile.mkdtemp", return_value="/tmp/wiki"), \
             caplog.at_level(logging.INFO, logger="root"):
            sync_wiki("github", "gitee", "src_owner", "tgt_owner",
                      "src_token", "tgt_token", "repo")

        push_calls = [c for c in run_calls if len(c) > 1 and c[1] == "push"]
        assert push_calls == [], f"Unexpected wiki push calls: {push_calls}"
        assert any("not available on target" in r.message for r in caplog.records)

    def test_proceeds_with_push_on_transient_lsremote_failure(self, caplog):
        """Wiki push should proceed when ls-remote fails for non-404 reasons."""
        run_calls = []

        def fake_run(args, **kwargs):
            run_calls.append(args)
            cmd = args[1] if len(args) > 1 else args[0]
            if cmd == "clone":
                return _make_process(returncode=0)
            elif cmd == "ls-remote":
                # Transient network error — no "not found" in stderr
                return _make_process(
                    returncode=128,
                    stderr="fatal: unable to access: Connection timed out",
                )
            return _make_process(returncode=0)

        with patch("lib.sync_repo.subprocess.run", side_effect=fake_run), \
             patch("lib.sync_repo.make_git_env",
                   return_value=({}, "/tmp/fake")), \
             patch("lib.sync_repo.shutil.rmtree"), \
             patch("lib.sync_repo.os.unlink"), \
             patch("lib.sync_repo.tempfile.mkdtemp", return_value="/tmp/wiki"), \
             caplog.at_level(logging.INFO, logger="root"):
            sync_wiki("github", "gitee", "src_owner", "tgt_owner",
                      "src_token", "tgt_token", "repo")

        # Should have logged a warning and still attempted push
        assert any("ls-remote for wiki on target failed" in r.message
                   for r in caplog.records)
        push_calls = [c for c in run_calls if len(c) > 1 and c[1] == "push"]
        assert len(push_calls) >= 1, "Should have attempted push despite ls-remote failure"


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

    def test_create_label_gitee_color_has_hash_prefix(self):
        """Gitee label creation must send color with '#' prefix (required by Gitee API)."""
        src_labels = [self._label("help wanted", color="008672")]
        tgt_labels = []
        mock_resp = _make_resp({}, status=201)
        with patch("lib.sync_repo.paginated_get",
                   side_effect=[src_labels, tgt_labels]), \
             patch("lib.sync_repo.api_request",
                   return_value=mock_resp) as mock_api:
            sync_labels("github", "gitee", "src", "tgt",
                        "tok1", "tok2", "repo")
        _, kwargs = mock_api.call_args
        assert kwargs["json"]["color"] == "#008672"

    def test_create_label_github_color_no_hash_prefix(self):
        """GitHub label creation must send color without '#' prefix."""
        src_labels = [self._label("help wanted", color="008672")]
        tgt_labels = []
        mock_resp = _make_resp({}, status=201)
        with patch("lib.sync_repo.paginated_get",
                   side_effect=[src_labels, tgt_labels]), \
             patch("lib.sync_repo.api_request",
                   return_value=mock_resp) as mock_api:
            sync_labels("gitee", "github", "src", "tgt",
                        "tok1", "tok2", "repo")
        _, kwargs = mock_api.call_args
        assert kwargs["json"]["color"] == "008672"

    def test_update_label_gitee_color_has_hash_prefix(self):
        """Gitee label update must send color with '#' prefix (required by Gitee API)."""
        src_labels = [self._label("bug", color="ff0000")]
        tgt_labels = [self._label("bug", color="0000ff")]
        mock_resp = _make_resp({}, status=200)
        with patch("lib.sync_repo.paginated_get",
                   side_effect=[src_labels, tgt_labels]), \
             patch("lib.sync_repo.api_request",
                   return_value=mock_resp) as mock_api:
            sync_labels("github", "gitee", "src", "tgt",
                        "tok1", "tok2", "repo")
        _, kwargs = mock_api.call_args
        assert kwargs["json"]["color"] == "#ff0000"

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

    def test_create_release_includes_target_commitish(self):
        """Payload sent to Gitee must include target_commitish from source."""
        src = self._release("v2.0.0")
        src["target_commitish"] = "main"
        tgt_releases = []
        mock_resp = _make_resp(self._release("v2.0.0"), status=201)
        with patch("lib.sync_repo.paginated_get",
                   side_effect=[[src], tgt_releases]), \
             patch("lib.sync_repo.api_request",
                   return_value=mock_resp) as mock_api:
            sync_releases("github", "gitee", "src", "tgt",
                          "tok1", "tok2", "repo")
        mock_api.assert_called_once()
        payload = mock_api.call_args[1]["json"]
        assert payload["target_commitish"] == "main"

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

    def test_creates_releases_in_chronological_order(self):
        """Releases should be created oldest-first so the newest becomes
        the 'latest release' on the target platform."""
        # API 通常返回最新在前（倒序）
        src_releases = [
            {**self._release("v3.0.0"), "created_at": "2024-03-01T00:00:00Z"},
            {**self._release("v2.0.0"), "created_at": "2024-02-01T00:00:00Z"},
            {**self._release("v1.0.0"), "created_at": "2024-01-01T00:00:00Z"},
        ]
        tgt_releases = []
        mock_resp = _make_resp(self._release("dummy"), status=201)
        with patch("lib.sync_repo.paginated_get",
                   side_effect=[src_releases, tgt_releases]), \
             patch("lib.sync_repo.api_request",
                   return_value=mock_resp) as mock_api:
            sync_releases("github", "gitee", "src", "tgt",
                          "tok1", "tok2", "repo")
        assert mock_api.call_count == 3
        created_tags = [
            call[1]["json"]["tag_name"]
            for call in mock_api.call_args_list
        ]
        # 应该按时间升序创建: v1 → v2 → v3
        assert created_tags == ["v1.0.0", "v2.0.0", "v3.0.0"]

    def test_prerelease_status_synced_on_create(self):
        """Pre-release flag should be preserved when creating a release."""
        src_releases = [
            {**self._release("v1.0.0-rc1", prerelease=True),
             "created_at": "2024-01-01T00:00:00Z"},
        ]
        tgt_releases = []
        mock_resp = _make_resp(self._release("v1.0.0-rc1"), status=201)
        with patch("lib.sync_repo.paginated_get",
                   side_effect=[src_releases, tgt_releases]), \
             patch("lib.sync_repo.api_request",
                   return_value=mock_resp) as mock_api:
            sync_releases("github", "gitee", "src", "tgt",
                          "tok1", "tok2", "repo")
        payload = mock_api.call_args[1]["json"]
        assert payload["prerelease"] is True

    def test_prerelease_status_synced_on_update(self):
        """Pre-release status change should trigger an update."""
        src_releases = [
            {**self._release("v1.0.0", prerelease=False),
             "created_at": "2024-01-01T00:00:00Z"},
        ]
        tgt_releases = [self._release("v1.0.0", prerelease=True)]
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
        payload = mock_api.call_args[1]["json"]
        assert payload["prerelease"] is False

    def test_empty_body_defaults_to_tag_name(self):
        """When source release has empty body, payload body defaults to tag."""
        src = self._release("v1.0.0", body="")
        src["target_commitish"] = "main"
        tgt_releases = []
        mock_resp = _make_resp(self._release("v1.0.0"), status=201)
        with patch("lib.sync_repo.paginated_get",
                   side_effect=[[src], tgt_releases]), \
             patch("lib.sync_repo.api_request",
                   return_value=mock_resp) as mock_api:
            sync_releases("github", "gitee", "src", "tgt",
                          "tok1", "tok2", "repo")
        payload = mock_api.call_args[1]["json"]
        assert payload["body"] == "v1.0.0"

    def test_empty_body_remains_empty_when_target_is_github(self):
        """When syncing to GitHub, empty source body should remain empty."""
        src = self._release("v1.0.0", body="")
        src["target_commitish"] = "main"
        tgt_releases = []
        mock_resp = _make_resp(self._release("v1.0.0"), status=201)
        with patch("lib.sync_repo.paginated_get",
                   side_effect=[[src], tgt_releases]), \
             patch("lib.sync_repo.api_request",
                   return_value=mock_resp) as mock_api:
            sync_releases("gitee", "github", "src", "tgt",
                          "tok1", "tok2", "repo")
        payload = mock_api.call_args[1]["json"]
        assert payload["body"] == ""

    def test_empty_target_commitish_omitted_from_payload(self):
        """When source release has no target_commitish, omit from payload."""
        src = self._release("v1.0.0", body="notes")
        src["target_commitish"] = ""
        tgt_releases = []
        mock_resp = _make_resp(self._release("v1.0.0"), status=201)
        with patch("lib.sync_repo.paginated_get",
                   side_effect=[[src], tgt_releases]), \
             patch("lib.sync_repo.api_request",
                   return_value=mock_resp) as mock_api:
            sync_releases("github", "gitee", "src", "tgt",
                          "tok1", "tok2", "repo")
        payload = mock_api.call_args[1]["json"]
        assert "target_commitish" not in payload

    def test_update_empty_body_defaults_to_tag_name_for_gitee(self):
        """When updating a Gitee release with empty source body, body falls back to tag."""
        src = {**self._release("v1.0.0", body=""), "target_commitish": "main"}
        tgt = {**self._release("v1.0.0", body="old notes"), "target_commitish": "main"}
        mock_resp = _make_resp({}, status=200)
        with patch("lib.sync_repo.paginated_get",
                   side_effect=[[src], [tgt]]), \
             patch("lib.sync_repo.api_request",
                   return_value=mock_resp) as mock_api:
            sync_releases("github", "gitee", "src", "tgt",
                          "tok1", "tok2", "repo")
        payload = mock_api.call_args[1]["json"]
        assert payload["body"] == "v1.0.0"

    def test_update_empty_body_remains_empty_for_github(self):
        """When updating a GitHub release with empty source body, body stays empty."""
        src = {**self._release("v1.0.0", body=""), "target_commitish": "main"}
        tgt = {**self._release("v1.0.0", body="old notes"), "target_commitish": "main"}
        mock_resp = _make_resp({}, status=200)
        with patch("lib.sync_repo.paginated_get",
                   side_effect=[[src], [tgt]]), \
             patch("lib.sync_repo.api_request",
                   return_value=mock_resp) as mock_api:
            sync_releases("gitee", "github", "src", "tgt",
                          "tok1", "tok2", "repo")
        payload = mock_api.call_args[1]["json"]
        assert payload["body"] == ""

    def test_update_skipped_when_gitee_target_body_already_tag_name(self):
        """No PATCH when Gitee target body already equals the fallback tag name.

        Without normalizing the desired body before the needs_update check, a
        release where the source body is empty but the target was previously set
        to the tag name would trigger a redundant PATCH on every sync run.
        """
        src = {**self._release("v1.0.0", body=""), "target_commitish": "main"}
        # Target body is already the tag name (result of a prior sync)
        tgt = {**self._release("v1.0.0", body="v1.0.0"), "target_commitish": "main"}
        with patch("lib.sync_repo.paginated_get",
                   side_effect=[[src], [tgt]]), \
             patch("lib.sync_repo.api_request") as mock_api:
            sync_releases("github", "gitee", "src", "tgt",
                          "tok1", "tok2", "repo")
        mock_api.assert_not_called()

    def test_create_failure_logs_response_body(self, caplog):
        """400 error should include response body in log for debugging."""
        src_releases = [self._release("v1.0.0", body="notes")]
        src_releases[0]["target_commitish"] = "main"
        tgt_releases = []
        mock_resp = _make_resp({"message": "tag not found"}, status=400)
        with patch("lib.sync_repo.paginated_get",
                   side_effect=[src_releases, tgt_releases]), \
             patch("lib.sync_repo.api_request",
                   return_value=mock_resp):
            with caplog.at_level(logging.WARNING):
                sync_releases("github", "gitee", "src", "tgt",
                              "tok1", "tok2", "repo")
        assert any(
            "400" in r.message and "tag not found" in r.message
            for r in caplog.records
        )


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

    def test_sync_wiki_clone_failure_info_uses_log_repo_name(self):
        """Wiki clone 失败 INFO 应使用 log_repo_name 而非 repo_name"""
        clone_proc = _make_process(returncode=128, stderr="not found")
        with patch("lib.sync_repo.subprocess.run",
                   return_value=clone_proc), \
             patch("lib.sync_repo.make_git_env",
                   return_value=({}, "/tmp/fake")), \
             patch("lib.sync_repo.shutil.rmtree"), \
             patch("lib.sync_repo.os.unlink"), \
             patch("lib.sync_repo.tempfile.mkdtemp",
                   return_value="/tmp/wiki"), \
             patch("lib.sync_repo.logging.info") as mock_info:
            sync_wiki("github", "gitee", "src_owner", "tgt_owner",
                      "src_token", "tgt_token", "secret-repo",
                      log_repo_name="[private]")
        info_messages = " ".join(str(c) for c in mock_info.call_args_list)
        assert "[private]" in info_messages
        assert "secret-repo" not in info_messages


# ===========================================================================
# _is_local_target / mirror_sync to local target
# ===========================================================================

from lib.sync_repo import _is_local_target


class TestIsLocalTarget:
    def test_https_url_is_remote(self):
        assert _is_local_target("https://github.com/o/r.git") is False

    def test_http_url_is_remote(self):
        assert _is_local_target("http://example.com/r.git") is False

    def test_ssh_url_is_remote(self):
        assert _is_local_target("ssh://git@host/r.git") is False
        assert _is_local_target("git@github.com:owner/r.git") is False
        assert _is_local_target("git://host/r.git") is False

    def test_scp_like_ssh_is_remote(self):
        # 通用 scp-like SSH 形式 [user@]host:path 也应识别为远程
        assert _is_local_target("user@host.example.com:owner/r.git") is False
        assert _is_local_target("host.example.com:owner/r.git") is False

    def test_file_url_case_insensitive_is_local(self):
        assert _is_local_target("FILE:///var/repos/foo.git") is True

    def test_linux_path_is_local(self):
        assert _is_local_target("/var/repos/foo.git") is True

    def test_relative_path_is_local(self):
        assert _is_local_target("repos/foo.git") is True

    def test_windows_path_is_local(self):
        assert _is_local_target("C:\\repos\\foo.git") is True
        assert _is_local_target("D:/repos/foo.git") is True

    def test_file_url_is_local(self):
        assert _is_local_target("file:///var/repos/foo.git") is True

    def test_empty_returns_false(self):
        assert _is_local_target("") is False
        assert _is_local_target(None) is False


class TestMirrorSyncToLocalTarget:
    """When target is local, make_git_env should NOT be called for target."""

    def test_local_target_skips_target_askpass(self):
        clone_proc = _make_process(returncode=0)
        push_all_proc = _make_process(returncode=0)
        push_tags_proc = _make_process(returncode=0)

        with patch("lib.sync_repo.subprocess.run",
                   side_effect=[clone_proc, push_all_proc, push_tags_proc]), \
             patch("lib.sync_repo._refs_already_in_sync", return_value=False), \
             patch("lib.sync_repo.make_git_env",
                   return_value=({}, "/tmp/fake_askpass")) as mock_env, \
             patch("lib.sync_repo.shutil.rmtree"), \
             patch("lib.sync_repo.os.unlink"), \
             patch("lib.sync_repo.tempfile.mkdtemp",
                   return_value="/tmp/testdir"):
            result = mirror_sync(
                "https://github.com/src/repo.git",
                "/var/repos/repo.git",  # local target
                "repo",
                "src_token", "",  # no target token
            )
        assert result == "success"
        # Only source side should call make_git_env
        assert mock_env.call_count == 1

    def test_local_windows_target_skips_target_askpass(self):
        clone_proc = _make_process(returncode=0)
        push_all_proc = _make_process(returncode=0)
        push_tags_proc = _make_process(returncode=0)

        with patch("lib.sync_repo.subprocess.run",
                   side_effect=[clone_proc, push_all_proc, push_tags_proc]), \
             patch("lib.sync_repo._refs_already_in_sync", return_value=False), \
             patch("lib.sync_repo.make_git_env",
                   return_value=({}, "/tmp/fake_askpass")) as mock_env, \
             patch("lib.sync_repo.shutil.rmtree"), \
             patch("lib.sync_repo.os.unlink"), \
             patch("lib.sync_repo.tempfile.mkdtemp",
                   return_value="/tmp/testdir"):
            result = mirror_sync(
                "https://github.com/src/repo.git",
                "C:\\repos\\repo.git",  # Windows local target
                "repo",
                "src_token", "",
            )
        assert result == "success"
        assert mock_env.call_count == 1
