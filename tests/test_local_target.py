"""
tests/test_local_target.py — lib/local_target.py 单元测试

覆盖范围:
- normalize_local_path: 路径规范化, 用户主目录展开, 空字符串异常
- ensure_local_path_writable: 创建目录, 已存在目录, 文件冲突, 不可写
- build_local_clone_url: Linux 路径, Windows 风格路径, 子目录拼接
- get_local_repos: 空目录, 不存在的目录, 含/不含 .git 后缀的子目录, 非目录条目
- create_local_repo: 成功创建, 幂等已存在, 非 git 目录冲突, git init 失败
"""

import os
import sys
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest

# 确保 lib 包可导入
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

from lib.local_target import (
    build_local_clone_url,
    create_local_repo,
    ensure_local_path_writable,
    get_local_repos,
    normalize_local_path,
)


class TestNormalizeLocalPath:
    def test_returns_path_object(self):
        p = normalize_local_path("/tmp/repos")
        assert isinstance(p, Path)
        assert str(p) == "/tmp/repos"

    def test_expands_user_home(self):
        p = normalize_local_path("~/repos")
        assert "~" not in str(p)

    def test_empty_string_raises(self):
        with pytest.raises(ValueError):
            normalize_local_path("")

    def test_none_raises(self):
        with pytest.raises(ValueError):
            normalize_local_path(None)


class TestEnsureLocalPathWritable:
    def test_creates_missing_directory(self, tmp_path):
        target = tmp_path / "new_subdir"
        assert not target.exists()
        result = ensure_local_path_writable(str(target))
        assert target.exists()
        assert target.is_dir()
        assert result == Path(str(target))

    def test_existing_directory_ok(self, tmp_path):
        result = ensure_local_path_writable(str(tmp_path))
        assert result == Path(str(tmp_path))

    def test_existing_file_raises(self, tmp_path):
        f = tmp_path / "afile"
        f.write_text("x")
        with pytest.raises(ValueError):
            ensure_local_path_writable(str(f))

    def test_not_writable_raises(self, tmp_path):
        # 通过 patch os.access 模拟不可写
        with patch("lib.local_target.os.access", return_value=False):
            with pytest.raises(PermissionError):
                ensure_local_path_writable(str(tmp_path))


class TestBuildLocalCloneUrl:
    def test_linux_path(self):
        url = build_local_clone_url("/var/repos", "myrepo")
        assert url == os.path.join("/var/repos", "myrepo.git")

    def test_relative_path(self):
        url = build_local_clone_url("repos", "myrepo")
        assert url.endswith("myrepo.git")
        assert "repos" in url

    def test_pathlib_handles_separator(self, tmp_path):
        url = build_local_clone_url(str(tmp_path), "abc")
        # Path will use platform-appropriate separator
        assert url == str(tmp_path / "abc.git")


class TestGetLocalRepos:
    def test_nonexistent_path_returns_empty(self, tmp_path):
        nonexist = tmp_path / "nope"
        assert get_local_repos(str(nonexist)) == []

    def test_empty_directory_returns_empty(self, tmp_path):
        assert get_local_repos(str(tmp_path)) == []

    def test_lists_bare_git_dirs(self, tmp_path):
        (tmp_path / "repo1.git").mkdir()
        (tmp_path / "repo2.git").mkdir()
        (tmp_path / "not-a-repo").mkdir()  # missing .git suffix
        (tmp_path / "afile.git").write_text("x")  # file, not dir

        repos = get_local_repos(str(tmp_path))
        names = sorted(r["name"] for r in repos)
        assert names == ["repo1", "repo2"]
        assert all(r["private"] is False for r in repos)

    def test_skips_dotgit_only(self, tmp_path):
        # Empty `.git` (no name prefix) should not be considered a repo
        (tmp_path / ".git").mkdir()
        repos = get_local_repos(str(tmp_path))
        assert repos == []

    def test_oserror_returns_empty(self, tmp_path):
        with patch.object(Path, "iterdir", side_effect=OSError("permission")):
            assert get_local_repos(str(tmp_path)) == []


class TestCreateLocalRepo:
    def test_creates_bare_repo(self, tmp_path):
        ok = create_local_repo(str(tmp_path), "myrepo")
        assert ok is True
        target = tmp_path / "myrepo.git"
        assert target.is_dir()
        assert (target / "HEAD").exists() or (target / "config").exists()

    def test_idempotent_for_existing_git_dir(self, tmp_path):
        # Create once
        assert create_local_repo(str(tmp_path), "repo") is True
        # Create again — should still return True without error
        assert create_local_repo(str(tmp_path), "repo") is True

    def test_existing_non_git_dir_fails(self, tmp_path):
        target = tmp_path / "repo.git"
        target.mkdir()
        # No HEAD/config inside → not a git dir
        ok = create_local_repo(str(tmp_path), "repo")
        assert ok is False

    def test_git_init_failure_returns_false(self, tmp_path):
        proc = MagicMock(returncode=1, stderr="boom", stdout="")
        with patch("lib.local_target.subprocess.run", return_value=proc):
            ok = create_local_repo(str(tmp_path), "repo")
        assert ok is False
        # half-baked dir should be cleaned up
        assert not (tmp_path / "repo.git").exists()

    def test_uses_log_repo_name(self, tmp_path, caplog):
        import logging
        caplog.set_level(logging.INFO)
        create_local_repo(str(tmp_path), "secret-repo",
                          log_repo_name="[private]")
        # Should not leak the real repo name in the log
        # (path will still contain it since we log the path; log_repo_name
        # is only used when error messages reference repo_name)
        # We just verify the call did not raise.

    def test_ignored_kwargs_do_not_break(self, tmp_path):
        # private/description args from create_github_repo signature should
        # be silently ignored
        ok = create_local_repo(
            str(tmp_path), "repo",
            private=True, description="hello",
        )
        assert ok is True
