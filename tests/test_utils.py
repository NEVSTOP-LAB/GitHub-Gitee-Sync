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

import os
import subprocess
import tempfile
from unittest.mock import MagicMock, patch, call

import pytest
import requests

from lib.utils import (
    GITHUB_API,
    GITEE_API,
    mask_token,
    build_clone_url,
    make_git_env,
    api_request,
    paginated_get,
    github_headers,
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
        assert headers["Authorization"] == "token mytoken"

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

    def test_gitee_adds_access_token_param(self):
        with patch("lib.utils.api_request",
                   return_value=self._make_resp([])) as mock_req:
            paginated_get("gitee", "mytoken", "/repos/owner/repo/labels")
            _, kwargs = mock_req.call_args
            assert kwargs.get("params", {}).get("access_token") == "mytoken"

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
# write_action_outputs
# ===========================================================================

class TestWriteActionOutputs:
    def test_writes_to_github_output_file(self, tmp_path):
        output_file = tmp_path / "output.txt"
        with patch.dict(os.environ, {"GITHUB_OUTPUT": str(output_file)}):
            write_action_outputs(5, 2, 1)

        content = output_file.read_text()
        assert "synced-count=5" in content
        assert "failed-count=2" in content
        assert "skipped-count=1" in content

    def test_no_write_without_env_var(self, tmp_path):
        env = {k: v for k, v in os.environ.items() if k != "GITHUB_OUTPUT"}
        with patch.dict(os.environ, env, clear=True):
            write_action_outputs(1, 0, 0)
        # Should not raise or crash


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
