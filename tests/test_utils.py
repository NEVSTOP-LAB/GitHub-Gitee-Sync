"""
tests/test_utils.py — lib/utils.py 单元测试

覆盖范围:
- mask_token(): Token 脱敏
- build_clone_url(): 无凭据 URL 构建
- make_git_env(): GIT_ASKPASS 脚本生成
- api_request(): HTTP 重试、Rate Limit 处理、5xx 重试
- paginated_get(): 分页请求逻辑
- github_headers(): 请求头构建
- write_action_outputs(): Action 输出写入
- check_git_installed(): Git 环境检测
"""

import logging
import os
import subprocess
import tempfile
from unittest.mock import MagicMock, patch, call

import pytest
import requests

from lib.utils import (
    GITHUB_API,
    GITEE_API,
    LogCollector,
    TokenMaskingFilter,
    mask_token,
    sanitize_response_text,
    build_clone_url,
    make_git_env,
    api_request,
    paginated_get,
    github_headers,
    gitee_headers,
    get_log_collector,
    write_action_outputs,
    check_git_installed,
)


# ===========================================================================
# mask_token
# ===========================================================================

class TestMaskToken:
    def test_masks_token_in_https_url(self):
        url = "https://ghp_abc123@github.com/owner/repo.git"
        assert mask_token(url) == "https://***@github.com/owner/repo.git"

    def test_masks_token_with_special_chars(self):
        url = "https://tok+en/abc@gitee.com/owner/repo.git"
        assert mask_token(url) == "https://***@gitee.com/owner/repo.git"

    def test_no_token_unchanged(self):
        url = "https://github.com/owner/repo.git"
        assert mask_token(url) == url

    def test_converts_non_string_to_str(self):
        assert mask_token(42) == "42"

    def test_multiple_tokens_masked(self):
        text = (
            "https://tok1@github.com/a.git "
            "and https://tok2@gitee.com/b.git"
        )
        result = mask_token(text)
        assert "tok1" not in result
        assert "tok2" not in result
        assert result.count("***") == 2


# ===========================================================================
# TokenMaskingFilter
# ===========================================================================

class TestTokenMaskingFilter:
    def _make_record(self, msg, args=()):
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=1, msg=msg, args=args, exc_info=None,
        )
        return record

    def test_masks_ghp_token(self):
        f = TokenMaskingFilter()
        record = self._make_record("Token is ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij")
        f.filter(record)
        assert "ghp_" not in record.msg
        assert "***" in record.msg

    def test_masks_gho_token(self):
        f = TokenMaskingFilter()
        record = self._make_record("Token is gho_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij")
        f.filter(record)
        assert "gho_" not in record.msg

    def test_masks_https_token_url(self):
        f = TokenMaskingFilter()
        record = self._make_record("URL: https://mytoken@github.com/repo.git")
        f.filter(record)
        assert "mytoken" not in record.msg

    def test_masks_access_token_param(self):
        f = TokenMaskingFilter()
        record = self._make_record("URL: https://api.gitee.com?access_token=secret123")
        f.filter(record)
        assert "secret123" not in record.msg

    def test_passes_safe_messages(self):
        f = TokenMaskingFilter()
        record = self._make_record("Normal log message without tokens")
        f.filter(record)
        assert record.msg == "Normal log message without tokens"

    def test_clears_args_after_masking(self):
        f = TokenMaskingFilter()
        token = "ghp_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
        record = self._make_record("Value: %s", (token,))
        f.filter(record)
        assert record.args == ()
        assert token not in record.msg
        assert "***" in record.msg


# ===========================================================================
# build_clone_url
# ===========================================================================

class TestBuildCloneUrl:
    def test_github_url(self):
        url = build_clone_url("github", "myowner", "myrepo")
        assert url == "https://github.com/myowner/myrepo.git"

    def test_gitee_url(self):
        url = build_clone_url("gitee", "myowner", "myrepo")
        assert url == "https://gitee.com/myowner/myrepo.git"

    def test_no_token_in_url(self):
        """安全保证：URL 中不含 @（无凭据）"""
        url = build_clone_url("github", "owner", "repo")
        assert "@" not in url

    def test_repo_name_preserved(self):
        url = build_clone_url("github", "owner", "my-special.repo")
        assert "my-special.repo" in url


# ===========================================================================
# make_git_env
# ===========================================================================

class TestMakeGitEnv:
    def test_returns_env_and_path(self):
        env, path = make_git_env("testtoken")
        try:
            assert isinstance(env, dict)
            assert isinstance(path, str)
            assert os.path.exists(path)
        finally:
            os.unlink(path)

    def test_askpass_path_set_in_env(self):
        env, path = make_git_env("testtoken")
        try:
            assert env["GIT_ASKPASS"] == path
        finally:
            os.unlink(path)

    def test_terminal_prompt_disabled(self):
        env, path = make_git_env("testtoken")
        try:
            assert env["GIT_TERMINAL_PROMPT"] == "0"
        finally:
            os.unlink(path)

    def test_askpass_script_executable(self):
        env, path = make_git_env("testtoken")
        try:
            assert os.access(path, os.X_OK)
        finally:
            os.unlink(path)

    def test_askpass_script_outputs_token(self):
        token = "ghp_1234567890abcdef"
        env, path = make_git_env(token)
        try:
            result = subprocess.run(
                [path], capture_output=True, text=True, timeout=5
            )
            assert result.stdout.strip() == token
        finally:
            os.unlink(path)

    def test_token_with_single_quote(self):
        """Token 含单引号不会导致 shell 注入"""
        token = "tok'en'test"
        env, path = make_git_env(token)
        try:
            result = subprocess.run(
                [path], capture_output=True, text=True, timeout=5
            )
            assert result.stdout.strip() == token
        finally:
            os.unlink(path)

    def test_token_with_special_chars(self):
        """Token 含特殊字符不会破坏脚本"""
        token = "tok+en/with$special&chars"
        env, path = make_git_env(token)
        try:
            result = subprocess.run(
                [path], capture_output=True, text=True, timeout=5
            )
            assert result.stdout.strip() == token
        finally:
            os.unlink(path)


# ===========================================================================
# github_headers
# ===========================================================================

class TestGithubHeaders:
    def test_contains_authorization(self):
        headers = github_headers("mytoken")
        assert headers["Authorization"] == "Bearer mytoken"

    def test_contains_accept(self):
        headers = github_headers("mytoken")
        assert "application/vnd.github" in headers["Accept"]


# ===========================================================================
# api_request
# ===========================================================================

class TestApiRequest:
    def test_successful_request(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}

        with patch("lib.utils.requests.request", return_value=mock_resp) as mock_req:
            resp = api_request("GET", "https://example.com/api")
            assert resp.status_code == 200
            mock_req.assert_called_once()

    def test_retries_on_connection_error(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}

        with patch("lib.utils.requests.request") as mock_req, \
             patch("lib.utils.time.sleep"):
            mock_req.side_effect = [
                requests.ConnectionError("connection refused"),
                mock_resp,
            ]
            resp = api_request("GET", "https://example.com/api", max_retries=3)
            assert resp.status_code == 200
            assert mock_req.call_count == 2

    def test_raises_after_max_retries_exhausted(self):
        with patch("lib.utils.requests.request") as mock_req, \
             patch("lib.utils.time.sleep"):
            mock_req.side_effect = requests.ConnectionError("always fails")
            with pytest.raises(requests.ConnectionError):
                api_request("GET", "https://example.com/api", max_retries=2)
            assert mock_req.call_count == 3  # initial + 2 retries

    def test_retries_on_503(self):
        mock_503 = MagicMock()
        mock_503.status_code = 503
        mock_503.headers = {}

        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.headers = {}

        with patch("lib.utils.requests.request") as mock_req, \
             patch("lib.utils.time.sleep"):
            mock_req.side_effect = [mock_503, mock_200]
            resp = api_request("GET", "https://example.com", max_retries=2)
            assert resp.status_code == 200
            assert mock_req.call_count == 2

    def test_retries_on_502(self):
        mock_502 = MagicMock()
        mock_502.status_code = 502
        mock_502.headers = {}

        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.headers = {}

        with patch("lib.utils.requests.request") as mock_req, \
             patch("lib.utils.time.sleep"):
            mock_req.side_effect = [mock_502, mock_200]
            resp = api_request("GET", "https://example.com", max_retries=2)
            assert resp.status_code == 200

    def test_returns_non_5xx_without_retry(self):
        """404 等错误不重试，直接返回"""
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        mock_resp.headers = {}

        with patch("lib.utils.requests.request", return_value=mock_resp) as mock_req:
            resp = api_request("GET", "https://example.com/api", max_retries=3)
            assert resp.status_code == 404
            assert mock_req.call_count == 1

    def test_rate_limit_handling(self):
        """429 with remaining=0 triggers wait then retry"""
        import time as _time

        mock_429 = MagicMock()
        mock_429.status_code = 429
        mock_429.headers = {
            "X-RateLimit-Remaining": "0",
            "X-RateLimit-Reset": str(int(_time.time()) + 2),
        }

        mock_200 = MagicMock()
        mock_200.status_code = 200
        mock_200.headers = {}

        with patch("lib.utils.requests.request") as mock_req, \
             patch("lib.utils.time.sleep") as mock_sleep:
            mock_req.side_effect = [mock_429, mock_200]
            resp = api_request("GET", "https://example.com", max_retries=3)
            assert resp.status_code == 200
            mock_sleep.assert_called()

    def test_defensive_rate_limit_header_parsing_missing(self):
        """缺失的 X-RateLimit 头不会导致崩溃"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}  # no rate limit headers

        with patch("lib.utils.requests.request", return_value=mock_resp):
            resp = api_request("GET", "https://example.com/api")
            assert resp.status_code == 200

    def test_defensive_rate_limit_header_parsing_non_numeric(self):
        """非数字 X-RateLimit 头不会导致 ValueError"""
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {
            "X-RateLimit-Remaining": "not-a-number",
            "X-RateLimit-Reset": "also-not-a-number",
        }

        with patch("lib.utils.requests.request", return_value=mock_resp):
            resp = api_request("GET", "https://example.com/api")
            assert resp.status_code == 200

    def test_sets_default_timeout(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}

        with patch("lib.utils.requests.request", return_value=mock_resp) as mock_req:
            api_request("GET", "https://example.com")
            _, kwargs = mock_req.call_args
            assert kwargs.get("timeout") == 30


# ===========================================================================
# paginated_get
# ===========================================================================

class TestPaginatedGet:
    def _make_resp(self, data, status=200):
        mock = MagicMock()
        mock.status_code = status
        mock.json.return_value = data
        mock.text = str(data)
        mock.headers = {}
        return mock

    def test_single_page(self):
        items = [{"id": 1}, {"id": 2}]
        responses = [self._make_resp(items), self._make_resp([])]
        with patch("lib.utils.api_request", side_effect=responses):
            result = paginated_get("github", "token", "/repos/owner/repo/labels")
        assert result == items

    def test_multiple_pages(self):
        page1 = [{"id": i} for i in range(100)]
        page2 = [{"id": 100}]
        page3 = []

        responses = [
            self._make_resp(page1),
            self._make_resp(page2),
            self._make_resp(page3),
        ]
        with patch("lib.utils.api_request", side_effect=responses):
            result = paginated_get("github", "token", "/repos/owner/repo/labels")
        assert len(result) == 101
        assert result[0]["id"] == 0
        assert result[100]["id"] == 100

    def test_stops_on_non_200(self):
        with patch("lib.utils.api_request",
                   return_value=self._make_resp([], status=403)):
            result = paginated_get("gitee", "token", "/repos/owner/repo/labels")
        assert result == []

    def test_warns_on_non_200(self):
        with patch("lib.utils.api_request",
                   return_value=self._make_resp([], status=404)), \
             patch("lib.utils.logging.warning") as mock_warn:
            paginated_get("github", "token", "/repos/owner/repo/labels")
            mock_warn.assert_called_once()
            warning_msg = str(mock_warn.call_args)
            assert "404" in warning_msg

    def test_warns_on_non_list_response(self):
        """API 返回 dict (如 {"message": "Not Found"}) 时应记录警告"""
        responses = [
            self._make_resp({"message": "Not Found"}),
        ]
        with patch("lib.utils.api_request", side_effect=responses), \
             patch("lib.utils.logging.warning") as mock_warn:
            result = paginated_get("github", "token", "/repos/owner/repo/labels")
        assert result == []
        mock_warn.assert_called_once()
        warning_msg = str(mock_warn.call_args)
        assert "non-list" in warning_msg.lower()

    def test_gitee_adds_bearer_header(self):
        with patch("lib.utils.api_request",
                   return_value=self._make_resp([])) as mock_req:
            paginated_get("gitee", "mytoken", "/repos/owner/repo/labels")
            _, kwargs = mock_req.call_args
            assert kwargs.get("headers", {}).get("Authorization") == "Bearer mytoken"

    def test_github_adds_auth_header(self):
        with patch("lib.utils.api_request",
                   return_value=self._make_resp([])) as mock_req:
            paginated_get("github", "mytoken", "/repos/owner/repo/labels")
            _, kwargs = mock_req.call_args
            assert "Authorization" in kwargs.get("headers", {})

    def test_extra_params_passed(self):
        with patch("lib.utils.api_request",
                   return_value=self._make_resp([])) as mock_req:
            paginated_get("github", "tok", "/repos/o/r/milestones",
                          extra_params={"state": "all"})
            _, kwargs = mock_req.call_args
            assert kwargs["params"]["state"] == "all"


# ===========================================================================
# LogCollector
# ===========================================================================

class TestLogCollector:
    def test_collects_warning_messages(self):
        collector = LogCollector()
        collector.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

        record = logging.LogRecord(
            name="test", level=logging.WARNING, pathname="test.py",
            lineno=1, msg="Something went wrong", args=(), exc_info=None,
        )
        collector.emit(record)
        assert "Something went wrong" in collector.get_log()

    def test_ignores_info_messages(self):
        collector = LogCollector()
        collector.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=1, msg="Normal info", args=(), exc_info=None,
        )
        # LogCollector level is WARNING, so INFO should be filtered by level
        if collector.filter(record) and record.levelno >= collector.level:
            collector.emit(record)
        assert collector.get_log() == ""

    def test_collects_error_messages(self):
        collector = LogCollector()
        collector.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

        record = logging.LogRecord(
            name="test", level=logging.ERROR, pathname="test.py",
            lineno=1, msg="Critical failure", args=(), exc_info=None,
        )
        collector.emit(record)
        log = collector.get_log()
        assert "[ERROR] Critical failure" in log

    def test_multiple_messages_joined(self):
        collector = LogCollector()
        collector.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))

        for msg in ("warn1", "warn2"):
            record = logging.LogRecord(
                name="test", level=logging.WARNING, pathname="test.py",
                lineno=1, msg=msg, args=(), exc_info=None,
            )
            collector.emit(record)
        log = collector.get_log()
        assert "warn1" in log
        assert "warn2" in log
        assert "\n" in log

    def test_empty_collector_returns_empty_string(self):
        collector = LogCollector()
        assert collector.get_log() == ""


# ===========================================================================
# write_action_outputs
# ===========================================================================

class TestWriteActionOutputs:
    def test_writes_to_github_output_file(self, tmp_path):
        output_file = tmp_path / "output.txt"
        with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output_file)}), \
             patch("lib.utils.get_log_collector", return_value=None):
            write_action_outputs(5, 2, 1)

        content = output_file.read_text()
        assert "synced-count=5" in content
        assert "failed-count=2" in content
        assert "skipped-count=1" in content

    def test_no_write_without_env_var(self, tmp_path):
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_OUTPUT"}
        env.pop("GITHUB_STEP_SUMMARY", None)
        with patch.dict(os.environ, env, clear=True), \
             patch("lib.utils.get_log_collector", return_value=None):
            write_action_outputs(1, 0, 0)
        # Should not raise or crash

    def test_writes_sync_log_to_output(self, tmp_path):
        """sync-log 应包含收集到的 WARNING+ 日志（使用随机化 heredoc 分隔符）"""
        output_file = tmp_path / "output.txt"
        collector = LogCollector()
        collector.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        record = logging.LogRecord(
            name="test", level=logging.WARNING, pathname="test.py",
            lineno=1, msg="test warning message", args=(), exc_info=None,
        )
        collector.emit(record)

        with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output_file)}), \
             patch("lib.utils.get_log_collector", return_value=collector):
            write_action_outputs(1, 0, 0)

        content = output_file.read_text()
        assert "sync-log<<SYNC_LOG_EOF_" in content
        assert "test warning message" in content

    def test_writes_step_summary(self, tmp_path):
        """应将摘要写入 $GITHUB_STEP_SUMMARY"""
        output_file = tmp_path / "output.txt"
        summary_file = tmp_path / "summary.md"

        with patch.dict(os.environ, {
            "GITHUB_OUTPUT": str(output_file),
            "GITHUB_STEP_SUMMARY": str(summary_file),
        }), patch("lib.utils.get_log_collector", return_value=None):
            write_action_outputs(3, 1, 2)

        summary = summary_file.read_text()
        assert "Sync Summary" in summary
        assert "3" in summary
        assert "1" in summary

    def test_step_summary_includes_warnings(self, tmp_path):
        """Step Summary 应包含 WARNING 日志"""
        summary_file = tmp_path / "summary.md"
        collector = LogCollector()
        collector.setFormatter(logging.Formatter("[%(levelname)s] %(message)s"))
        record = logging.LogRecord(
            name="test", level=logging.WARNING, pathname="test.py",
            lineno=1, msg="a warning", args=(), exc_info=None,
        )
        collector.emit(record)

        with patch.dict(os.environ, {
            "GITHUB_STEP_SUMMARY": str(summary_file),
        }), patch("lib.utils.get_log_collector", return_value=collector):
            write_action_outputs(1, 0, 0)

        summary = summary_file.read_text()
        assert "Warnings & Errors" in summary
        assert "a warning" in summary


# ===========================================================================
# check_git_installed
# ===========================================================================

class TestCheckGitInstalled:
    def test_passes_when_git_available(self):
        """git is expected to be available in the test environment"""
        check_git_installed()  # Should not raise

    def test_raises_when_git_not_found(self):
        with patch("lib.utils.subprocess.run",
                   side_effect=FileNotFoundError("git not found")):
            with pytest.raises(Exception, match="Git is not installed"):
                check_git_installed()


# ===========================================================================
# sanitize_response_text
# ===========================================================================

class TestSanitizeResponseText:
    def test_truncates_long_text(self):
        long_text = "a" * 500
        result = sanitize_response_text(long_text, max_len=200)
        assert len(result) == 200

    def test_masks_token_in_response(self):
        text = 'error: https://secret_token@github.com/repo.git failed'
        result = sanitize_response_text(text)
        assert "secret_token" not in result
        assert "***" in result

    def test_replaces_newlines(self):
        text = "line1\nline2\nline3"
        result = sanitize_response_text(text)
        assert "\n" not in result

    def test_empty_text_returns_empty(self):
        assert sanitize_response_text("") == ""
        assert sanitize_response_text(None) == ""


# ===========================================================================
# TokenMaskingFilter — 新增模式测试
# ===========================================================================

class TestTokenMaskingFilterExtended:
    def _make_record(self, msg, args=()):
        record = logging.LogRecord(
            name="test", level=logging.INFO, pathname="test.py",
            lineno=1, msg=msg, args=args, exc_info=None,
        )
        return record

    def test_masks_ghs_token(self):
        """GitHub Actions 内置 Token (ghs_) 应被脱敏"""
        f = TokenMaskingFilter()
        record = self._make_record(
            "Token is ghs_ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghij"
        )
        f.filter(record)
        assert "ghs_" not in record.msg
        assert "***" in record.msg

    def test_masks_bearer_token(self):
        """Bearer Token 模式应被脱敏"""
        f = TokenMaskingFilter()
        record = self._make_record(
            "Header: Bearer my_secret_token_value"
        )
        f.filter(record)
        assert "my_secret_token_value" not in record.msg
        assert "***" in record.msg


# ===========================================================================
# write_action_outputs — 安全性测试
# ===========================================================================

class TestWriteActionOutputsSecurity:
    def test_heredoc_delimiter_is_randomized(self, tmp_path):
        """heredoc 分隔符应每次不同，防止注入攻击"""
        output_file1 = tmp_path / "output1.txt"
        output_file2 = tmp_path / "output2.txt"
        collector = LogCollector()

        with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output_file1)}), \
             patch("lib.utils.get_log_collector", return_value=collector):
            write_action_outputs(1, 0, 0)

        with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output_file2)}), \
             patch("lib.utils.get_log_collector", return_value=collector):
            write_action_outputs(1, 0, 0)

        content1 = output_file1.read_text()
        content2 = output_file2.read_text()
        # 提取分隔符部分（SYNC_LOG_EOF_<hex>）
        import re
        delims1 = re.findall(r'SYNC_LOG_EOF_[a-f0-9]+', content1)
        delims2 = re.findall(r'SYNC_LOG_EOF_[a-f0-9]+', content2)
        assert len(delims1) >= 1
        assert len(delims2) >= 1
        assert delims1[0] != delims2[0], "Delimiter should be randomized"

    def test_log_with_fake_delimiter_does_not_inject(self, tmp_path):
        """日志内容包含旧固定分隔符字符串时不应导致 heredoc 提前终止"""
        output_file = tmp_path / "output.txt"
        collector = LogCollector()
        collector.setFormatter(logging.Formatter("%(message)s"))
        record = logging.LogRecord(
            name="test", level=logging.WARNING, pathname="test.py",
            lineno=1, msg="SYNC_LOG_EOF\nmalicious-key=injected",
            args=(), exc_info=None,
        )
        collector.emit(record)

        with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output_file)}), \
             patch("lib.utils.get_log_collector", return_value=collector):
            write_action_outputs(1, 0, 0)

        content = output_file.read_text()
        # 随机化分隔符应正确包裹含有旧分隔符字符串的内容
        # 验证: 开始和结束的分隔符应相同，且包含随机后缀
        import re
        delimiters = re.findall(r'SYNC_LOG_EOF_[a-f0-9]+', content)
        assert len(delimiters) == 2, "Should have opening and closing delimiter"
        assert delimiters[0] == delimiters[1], "Opening and closing delimiters must match"
        # 旧的固定字符串 "SYNC_LOG_EOF"（不带随机后缀）不应作为分隔符
        # 验证日志内容确实在 heredoc 边界内
        assert "SYNC_LOG_EOF\n" in content  # 旧字符串在内容中
        assert "malicious-key=injected" in content  # 被安全包裹

    def test_step_summary_escapes_backticks(self, tmp_path):
        """Step Summary 中的反引号应被转义，防止 markdown 注入"""
        summary_file = tmp_path / "summary.md"
        collector = LogCollector()
        collector.setFormatter(logging.Formatter("%(message)s"))
        record = logging.LogRecord(
            name="test", level=logging.WARNING, pathname="test.py",
            lineno=1,
            msg="```\n<script>alert('xss')</script>\n```",
            args=(), exc_info=None,
        )
        collector.emit(record)

        with patch.dict(os.environ, {
            "GITHUB_STEP_SUMMARY": str(summary_file),
        }), patch("lib.utils.get_log_collector", return_value=collector):
            write_action_outputs(1, 0, 0)

        summary = summary_file.read_text()
        # 反引号应被转义
        assert "\\`\\`\\`" in summary


# ===========================================================================
# paginated_get — 安全上限测试
# ===========================================================================

class TestPaginatedGetSafetyLimit:
    @patch("lib.utils.api_request")
    def test_stops_on_empty_response(self, mock_request):
        """分页应在空响应时停止"""
        # 模拟前 5 页有数据，第 6 页返回空列表
        responses = [MagicMock(status_code=200,
                               json=MagicMock(return_value=[{"id": i}]))
                     for i in range(5)]
        responses.append(MagicMock(status_code=200,
                                   json=MagicMock(return_value=[])))
        mock_request.side_effect = responses

        result = paginated_get("github", "token", "/test")
        assert len(result) == 5
        assert mock_request.call_count == 6

    @patch("lib.utils.api_request")
    def test_enforces_max_pages_safety_limit(self, mock_request):
        """分页安全上限: 即使 API 持续返回非空数据也应在 MAX_PAGES 处停止"""
        # 模拟 API 始终返回非空数据（不会自然停止）
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = [{"id": 1}]
        mock_request.return_value = mock_resp

        # 临时将 MAX_PAGES 设为较小值以加速测试
        import lib.utils as utils_module
        original_code = utils_module.paginated_get

        # 使用 monkeypatch 方式: 在函数内部引用的 MAX_PAGES 是局部变量
        # 我们需要通过调用函数并计算 mock 调用次数来验证
        # 由于 MAX_PAGES=500 太大，我们验证调用次数不超过合理范围
        result = paginated_get("github", "token", "/test")
        # 应在 500 页时停止（每页 1 条 = 500 条结果）
        assert len(result) == 500
        assert mock_request.call_count == 500
