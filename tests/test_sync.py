"""
tests/test_sync.py — sync.py 单元测试

覆盖范围:
- parse_args(): 必填参数校验, 布尔值转换, 逗号分隔列表解析, 环境变量读取
- sync_one_direction(): 排除仓库过滤, 创建缺失仓库, 目标仓库已存在, dry-run 模式
- sync_all(): 退出码设计 (0/1/2), 方向选择 (github2gitee/gitee2github/both)
"""

import sys
import os
from argparse import Namespace
from unittest.mock import MagicMock, patch, call

import pytest

# sync.py 在根目录下，需要确保可导入
sys.path.insert(0, os.path.dirname(os.path.dirname(__file__)))

import sync as sync_module
from sync import parse_args, sync_all, sync_one_direction


# ===========================================================================
# parse_args
# ===========================================================================

class TestParseArgs:
    """测试命令行参数解析。通过 patch sys.argv 模拟 CLI 调用。"""

    def _parse(self, argv_extra):
        argv = [
            "sync.py",
            "--github-owner", "ghowner",
            "--github-token", "ghtoken",
            "--gitee-owner", "giteeowner",
            "--gitee-token", "giteetoken",
        ] + argv_extra
        with patch("sys.argv", argv):
            return parse_args()

    def test_required_params_parsed(self):
        args = self._parse([])
        assert args.github_owner == "ghowner"
        assert args.github_token == "ghtoken"
        assert args.gitee_owner == "giteeowner"
        assert args.gitee_token == "giteetoken"

    def test_defaults(self):
        args = self._parse([])
        assert args.account_type == "user"
        assert args.include_private is True
        assert args.direction == "github2gitee"
        assert args.create_missing_repos is True
        assert args.dry_run is False
        assert args.exclude_repos == set()
        assert args.sync_extra == set()

    def test_bool_conversion_true(self):
        for val in ("true", "True", "1", "yes"):
            args = self._parse([f"--include-private={val}"])
            assert args.include_private is True

    def test_bool_conversion_false(self):
        for val in ("false", "False", "0", "no"):
            args = self._parse([f"--include-private={val}"])
            assert args.include_private is False

    def test_dry_run_parsed(self):
        args = self._parse(["--dry-run=true"])
        assert args.dry_run is True

    def test_exclude_repos_parsed_as_set(self):
        args = self._parse(["--exclude-repos=repo1,repo2,repo3"])
        assert args.exclude_repos == {"repo1", "repo2", "repo3"}

    def test_sync_extra_parsed_as_set(self):
        args = self._parse(["--sync-extra=releases,wiki,labels"])
        assert args.sync_extra == {"releases", "wiki", "labels"}

    def test_sync_extra_invalid_values_warned_and_filtered(self):
        """无效的 sync-extra 值应被过滤并记录警告"""
        with patch("sync.logging.warning") as mock_warn:
            args = self._parse(["--sync-extra=releases,release,invalid"])
        assert args.sync_extra == {"releases"}
        mock_warn.assert_called_once()
        warning_msg = str(mock_warn.call_args)
        assert "release" in warning_msg or "invalid" in warning_msg

    def test_empty_exclude_repos_is_empty_set(self):
        args = self._parse(["--exclude-repos="])
        assert args.exclude_repos == set()

    def test_include_repos_parsed_as_set(self):
        args = self._parse(["--include-repos=repo1,repo2,repo3"])
        assert args.include_repos == {"repo1", "repo2", "repo3"}

    def test_empty_include_repos_is_empty_set(self):
        args = self._parse(["--include-repos="])
        assert args.include_repos == set()

    def test_include_repos_default_is_empty_set(self):
        args = self._parse([])
        assert args.include_repos == set()

    def test_include_and_exclude_warns(self):
        """同时设置 include-repos 和 exclude-repos 时应记录警告"""
        with patch("sync.logging.warning") as mock_warn:
            args = self._parse([
                "--include-repos=repo1",
                "--exclude-repos=repo2",
            ])
        assert args.include_repos == {"repo1"}
        assert args.exclude_repos == {"repo2"}
        # Should have warning about both being set
        warn_messages = [str(c) for c in mock_warn.call_args_list]
        assert any("include-repos" in msg.lower() for msg in warn_messages)

    def test_missing_required_param_exits(self):
        with patch("sys.argv", ["sync.py", "--github-owner", "o"]):
            with pytest.raises(SystemExit):
                parse_args()

    def test_env_var_fallback(self):
        env = {
            "GITHUB_OWNER": "env_gh_owner",
            "GITHUB_TOKEN": "env_gh_token",
            "GITEE_OWNER": "env_gitee_owner",
            "GITEE_TOKEN": "env_gitee_token",
        }
        with patch.dict(os.environ, env), \
             patch("sys.argv", ["sync.py"]):
            args = parse_args()
        assert args.github_owner == "env_gh_owner"
        assert args.gitee_owner == "env_gitee_owner"

    def test_direction_choices(self):
        for direction in ("github2gitee", "gitee2github", "both"):
            args = self._parse([f"--direction={direction}"])
            assert args.direction == direction

    def test_invalid_direction_exits(self):
        with pytest.raises(SystemExit):
            self._parse(["--direction=invalid"])


# ===========================================================================
# sync_one_direction
# ===========================================================================

class TestSyncOneDirection:
    def _common_kwargs(self, **overrides):
        defaults = dict(
            source_platform="github",
            target_platform="gitee",
            source_owner="ghowner",
            target_owner="giteeowner",
            source_token="ghtoken",
            target_token="giteetoken",
            account_type="user",
            include_private=True,
            include_repos=set(),
            exclude_repos=set(),
            create_missing_repos=True,
            sync_extra=set(),
            dry_run=False,
        )
        defaults.update(overrides)
        return defaults

    def test_empty_source_repos(self):
        with patch("sync.get_github_repos", return_value=[]), \
             patch("sync.get_gitee_repos", return_value=[]):
            synced, failed, skipped, failed_repos = sync_one_direction(
                **self._common_kwargs()
            )
        assert synced == 0
        assert failed == 0
        assert skipped == 0

    def test_exclude_repos_filtered(self):
        src_repos = [{"name": "keep"}, {"name": "exclude-me"}]
        tgt_repos = [{"name": "keep"}, {"name": "exclude-me"}]

        with patch("sync.get_github_repos", return_value=src_repos), \
             patch("sync.get_gitee_repos", return_value=tgt_repos), \
             patch("sync.mirror_sync", return_value="success"), \
             patch("sync.sync_repo_metadata"):
            synced, failed, skipped, failed_repos = sync_one_direction(
                **self._common_kwargs(exclude_repos={"exclude-me"})
            )
        assert synced == 1

    def test_skips_repo_when_create_missing_false_and_not_on_target(self):
        src_repos = [{"name": "newrepo", "private": False, "description": ""}]

        with patch("sync.get_github_repos", return_value=src_repos), \
             patch("sync.get_gitee_repos", return_value=[]):
            synced, failed, skipped, failed_repos = sync_one_direction(
                **self._common_kwargs(create_missing_repos=False)
            )
        assert skipped == 1
        assert synced == 0

    def test_creates_missing_repo_and_syncs(self):
        src_repos = [{"name": "newrepo", "private": False, "description": ""}]

        with patch("sync.get_github_repos", return_value=src_repos), \
             patch("sync.get_gitee_repos", return_value=[]), \
             patch("sync.create_gitee_repo", return_value=True) as mock_create, \
             patch("sync.mirror_sync", return_value="success"), \
             patch("sync.sync_repo_metadata"):
            synced, failed, skipped, failed_repos = sync_one_direction(
                **self._common_kwargs()
            )
        mock_create.assert_called_once()
        assert synced == 1

    def test_failed_repo_create_marks_failed(self):
        src_repos = [{"name": "newrepo", "private": False, "description": ""}]

        with patch("sync.get_github_repos", return_value=src_repos), \
             patch("sync.get_gitee_repos", return_value=[]), \
             patch("sync.create_gitee_repo", return_value=False):
            synced, failed, skipped, failed_repos = sync_one_direction(
                **self._common_kwargs()
            )
        assert failed == 1
        assert len(failed_repos) == 1

    def test_mirror_sync_failure_marks_repo_failed(self):
        src_repos = [{"name": "repo1", "private": False, "description": ""}]
        tgt_repos = [{"name": "repo1"}]

        with patch("sync.get_github_repos", return_value=src_repos), \
             patch("sync.get_gitee_repos", return_value=tgt_repos), \
             patch("sync.mirror_sync", return_value="failed"), \
             patch("sync.sync_repo_metadata"):
            synced, failed, skipped, failed_repos = sync_one_direction(
                **self._common_kwargs()
            )
        assert failed == 1
        assert synced == 0

    def test_empty_mirror_repo_counted_as_skipped(self):
        src_repos = [{"name": "empty-repo", "private": False, "description": ""}]
        tgt_repos = [{"name": "empty-repo"}]

        with patch("sync.get_github_repos", return_value=src_repos), \
             patch("sync.get_gitee_repos", return_value=tgt_repos), \
             patch("sync.mirror_sync", return_value="empty"):
            synced, failed, skipped, failed_repos = sync_one_direction(
                **self._common_kwargs()
            )
        assert skipped == 1

    def test_dry_run_skips_repo_creation(self):
        src_repos = [{"name": "newrepo", "private": False, "description": ""}]

        with patch("sync.get_github_repos", return_value=src_repos), \
             patch("sync.get_gitee_repos", return_value=[]), \
             patch("sync.create_gitee_repo") as mock_create, \
             patch("sync.mirror_sync", return_value="success"), \
             patch("sync.sync_repo_metadata"):
            sync_one_direction(**self._common_kwargs(dry_run=True))
        mock_create.assert_not_called()

    def test_sync_extra_calls_sync_extras(self):
        src_repos = [{"name": "repo1", "private": False, "description": ""}]
        tgt_repos = [{"name": "repo1"}]

        with patch("sync.get_github_repos", return_value=src_repos), \
             patch("sync.get_gitee_repos", return_value=tgt_repos), \
             patch("sync.mirror_sync", return_value="success"), \
             patch("sync.sync_repo_metadata"), \
             patch("sync.sync_extras") as mock_extras:
            sync_one_direction(**self._common_kwargs(sync_extra={"labels"}))
        mock_extras.assert_called_once()

    def test_include_repos_filters_to_allow_list(self):
        """include_repos 设置后，只同步允许列表中的仓库"""
        src_repos = [
            {"name": "allowed"},
            {"name": "not-allowed"},
            {"name": "also-allowed"},
        ]
        tgt_repos = [{"name": "allowed"}, {"name": "also-allowed"}]

        with patch("sync.get_github_repos", return_value=src_repos), \
             patch("sync.get_gitee_repos", return_value=tgt_repos), \
             patch("sync.mirror_sync", return_value="success"), \
             patch("sync.sync_repo_metadata"):
            synced, failed, skipped, failed_repos = sync_one_direction(
                **self._common_kwargs(
                    include_repos={"allowed", "also-allowed"}
                )
            )
        assert synced == 2

    def test_include_repos_takes_precedence_over_exclude(self):
        """include_repos 优先于 exclude_repos"""
        src_repos = [{"name": "repo1"}, {"name": "repo2"}]
        tgt_repos = [{"name": "repo1"}, {"name": "repo2"}]

        with patch("sync.get_github_repos", return_value=src_repos), \
             patch("sync.get_gitee_repos", return_value=tgt_repos), \
             patch("sync.mirror_sync", return_value="success"), \
             patch("sync.sync_repo_metadata"):
            synced, failed, skipped, failed_repos = sync_one_direction(
                **self._common_kwargs(
                    include_repos={"repo1"},
                    exclude_repos={"repo1"},
                )
            )
        # include_repos wins: repo1 is synced despite being in exclude
        assert synced == 1

    def test_include_repos_empty_syncs_all(self):
        """include_repos 为空时同步全部仓库（不过滤）"""
        src_repos = [{"name": "repo1"}, {"name": "repo2"}]
        tgt_repos = [{"name": "repo1"}, {"name": "repo2"}]

        with patch("sync.get_github_repos", return_value=src_repos), \
             patch("sync.get_gitee_repos", return_value=tgt_repos), \
             patch("sync.mirror_sync", return_value="success"), \
             patch("sync.sync_repo_metadata"):
            synced, failed, skipped, failed_repos = sync_one_direction(
                **self._common_kwargs(include_repos=set())
            )
        assert synced == 2


# ===========================================================================
# sync_all — exit codes
# ===========================================================================

class TestSyncAll:
    def _make_args(self, direction="github2gitee", dry_run=False):
        args = Namespace(
            direction=direction,
            github_owner="ghowner",
            gitee_owner="giteeowner",
            github_token="ghtoken",
            gitee_token="giteetoken",
            account_type="user",
            include_private=True,
            include_repos=set(),
            exclude_repos=set(),
            create_missing_repos=True,
            sync_extra=set(),
            dry_run=dry_run,
        )
        return args

    def test_exit_code_0_when_all_succeed(self):
        with patch("sync.sync_one_direction",
                   return_value=(3, 0, 0, [])), \
             patch("sync.write_action_outputs"):
            code = sync_all(self._make_args())
        assert code == 0

    def test_exit_code_1_when_partial_failure(self):
        with patch("sync.sync_one_direction",
                   return_value=(2, 1, 0, [("repo", "reason")])), \
             patch("sync.write_action_outputs"):
            code = sync_all(self._make_args())
        assert code == 1

    def test_exit_code_2_when_all_failed(self):
        with patch("sync.sync_one_direction",
                   return_value=(0, 3, 0, [("r1", "e"), ("r2", "e"), ("r3", "e")])), \
             patch("sync.write_action_outputs"):
            code = sync_all(self._make_args())
        assert code == 2

    def test_github2gitee_calls_sync_once(self):
        with patch("sync.sync_one_direction",
                   return_value=(1, 0, 0, [])) as mock_sync, \
             patch("sync.write_action_outputs"):
            sync_all(self._make_args(direction="github2gitee"))
        assert mock_sync.call_count == 1
        call_args = mock_sync.call_args[0]
        assert call_args[0] == "github"
        assert call_args[1] == "gitee"

    def test_gitee2github_calls_sync_once(self):
        with patch("sync.sync_one_direction",
                   return_value=(1, 0, 0, [])) as mock_sync, \
             patch("sync.write_action_outputs"):
            sync_all(self._make_args(direction="gitee2github"))
        assert mock_sync.call_count == 1
        call_args = mock_sync.call_args[0]
        assert call_args[0] == "gitee"
        assert call_args[1] == "github"

    def test_both_calls_sync_twice(self):
        with patch("sync.sync_one_direction",
                   return_value=(1, 0, 0, [])) as mock_sync, \
             patch("sync.write_action_outputs"):
            sync_all(self._make_args(direction="both"))
        assert mock_sync.call_count == 2

    def test_writes_action_outputs(self):
        with patch("sync.sync_one_direction",
                   return_value=(5, 2, 1, [])), \
             patch("sync.write_action_outputs") as mock_out:
            sync_all(self._make_args())
        mock_out.assert_called_once_with(5, 2, 1)

    def test_dry_run_mode_logged(self):
        with patch("sync.sync_one_direction",
                   return_value=(0, 0, 0, [])), \
             patch("sync.write_action_outputs"), \
             patch("sync.logging.info") as mock_log:
            sync_all(self._make_args(dry_run=True))
        log_messages = [str(c) for c in mock_log.call_args_list]
        assert any("DRY-RUN" in msg for msg in log_messages)

    def test_all_skipped_returns_0_with_warning(self):
        """全部跳过时退出码为 0 但应输出警告"""
        with patch("sync.sync_one_direction",
                   return_value=(0, 0, 5, [])), \
             patch("sync.write_action_outputs"), \
             patch("sync.logging.warning") as mock_warn:
            code = sync_all(self._make_args())
        assert code == 0
        mock_warn.assert_called_once()
        warning_msg = str(mock_warn.call_args)
        assert "skipped" in warning_msg.lower()
