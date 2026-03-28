"""
tests/test_gitee_api.py — lib/gitee_api.py 单元测试

覆盖范围:
- validate_gitee_token(): Token 验证 (200, 401, 其他错误码, 网络异常)
- get_gitee_repos(): 仓库列表获取, 分页, owner 过滤, 私有仓库过滤
- create_gitee_repo(): 仓库创建 (201 成功, 422 已存在, 失败)
- get_gitee_repo_details(): 仓库详情获取
- update_gitee_repo_metadata(): 元信息更新
"""

from unittest.mock import MagicMock, patch

import pytest
import requests

from lib.gitee_api import (
    create_gitee_repo,
    get_gitee_repo_details,
    get_gitee_repos,
    update_gitee_repo_metadata,
    validate_gitee_token,
)
from lib.utils import GITEE_API


def _make_resp(data, status=200):
    mock = MagicMock()
    mock.status_code = status
    mock.json.return_value = data
    mock.text = str(data)
    mock.headers = {}
    return mock


# ===========================================================================
# validate_gitee_token
# ===========================================================================

class TestValidateGiteeToken:
    def test_valid_token_returns_login(self):
        with patch("lib.gitee_api.requests.get",
                   return_value=_make_resp({"login": "giteeuser"})):
            user = validate_gitee_token("valid_token")
        assert user == "giteeuser"

    def test_invalid_token_raises_on_401(self):
        with patch("lib.gitee_api.requests.get",
                   return_value=_make_resp({}, status=401)):
            with pytest.raises(Exception, match="401"):
                validate_gitee_token("bad_token")

    def test_other_error_code_raises(self):
        with patch("lib.gitee_api.requests.get",
                   return_value=_make_resp({}, status=500)):
            with pytest.raises(Exception, match="500"):
                validate_gitee_token("token")

    def test_network_error_raises(self):
        with patch("lib.gitee_api.requests.get",
                   side_effect=requests.ConnectionError("no network")):
            with pytest.raises(Exception, match="network error"):
                validate_gitee_token("token")

    def test_unknown_login_defaults_to_unknown(self):
        with patch("lib.gitee_api.requests.get",
                   return_value=_make_resp({})):
            user = validate_gitee_token("token")
        assert user == "unknown"


# ===========================================================================
# get_gitee_repos
# ===========================================================================

class TestGetGiteeRepos:
    def _repo(self, name, owner="myowner", private=False, description=""):
        return {
            "name": name,
            "namespace": {"path": owner},
            "private": private,
            "description": description,
        }

    def test_returns_repos_for_user(self):
        page1 = [self._repo("repo1"), self._repo("repo2")]
        page2 = []
        responses = [_make_resp(page1), _make_resp(page2)]
        with patch("lib.gitee_api.api_request", side_effect=responses):
            repos = get_gitee_repos("myowner", "token", "user")
        assert len(repos) == 2
        assert repos[0]["name"] == "repo1"

    def test_org_uses_org_endpoint(self):
        with patch("lib.gitee_api.api_request",
                   return_value=_make_resp([])) as mock_req:
            get_gitee_repos("myorg", "token", "org")
            url = mock_req.call_args[0][1]
            assert "/orgs/myorg/repos" in url

    def test_user_uses_user_repos_endpoint(self):
        with patch("lib.gitee_api.api_request",
                   return_value=_make_resp([])) as mock_req:
            get_gitee_repos("myowner", "token", "user")
            url = mock_req.call_args[0][1]
            assert "/user/repos" in url

    def test_filters_out_wrong_owner_via_namespace(self):
        """namespace.path 不匹配时应被过滤"""
        repos = [
            self._repo("myrepo", owner="myowner"),
            self._repo("otherrepo", owner="otherowner"),
        ]
        responses = [_make_resp(repos), _make_resp([])]
        with patch("lib.gitee_api.api_request", side_effect=responses):
            result = get_gitee_repos("myowner", "token", "user")
        assert len(result) == 1
        assert result[0]["name"] == "myrepo"

    def test_excludes_private_when_include_private_false(self):
        repos = [
            self._repo("public-repo", private=False),
            self._repo("private-repo", private=True),
        ]
        responses = [_make_resp(repos), _make_resp([])]
        with patch("lib.gitee_api.api_request", side_effect=responses):
            result = get_gitee_repos("myowner", "token", "user",
                                     include_private=False)
        assert len(result) == 1
        assert result[0]["name"] == "public-repo"

    def test_includes_private_by_default(self):
        repos = [
            self._repo("public-repo", private=False),
            self._repo("private-repo", private=True),
        ]
        responses = [_make_resp(repos), _make_resp([])]
        with patch("lib.gitee_api.api_request", side_effect=responses):
            result = get_gitee_repos("myowner", "token", "user")
        assert len(result) == 2

    def test_raises_on_api_error(self):
        with patch("lib.gitee_api.api_request",
                   return_value=_make_resp({}, status=403)):
            with pytest.raises(Exception, match="403"):
                get_gitee_repos("owner", "token", "user")

    def test_bearer_header_in_request(self):
        with patch("lib.gitee_api.api_request",
                   return_value=_make_resp([])) as mock_req:
            get_gitee_repos("owner", "mytoken", "user")
            _, kwargs = mock_req.call_args
            assert kwargs["headers"]["Authorization"] == "Bearer mytoken"


# ===========================================================================
# create_gitee_repo
# ===========================================================================

class TestCreateGiteeRepo:
    def test_creates_repo_successfully(self):
        with patch("lib.gitee_api.api_request",
                   return_value=_make_resp({"name": "newrepo"}, status=201)):
            ok = create_gitee_repo("owner", "token", "newrepo", False, "", "user")
        assert ok is True

    def test_returns_true_when_already_exists_422(self):
        with patch("lib.gitee_api.api_request",
                   return_value=_make_resp({}, status=422)):
            ok = create_gitee_repo("owner", "token", "newrepo", False, "", "user")
        assert ok is True

    def test_returns_false_on_failure(self):
        with patch("lib.gitee_api.api_request",
                   return_value=_make_resp({}, status=500)):
            ok = create_gitee_repo("owner", "token", "newrepo", False, "", "user")
        assert ok is False

    def test_truncates_long_description(self):
        long_desc = "x" * 300
        with patch("lib.gitee_api.api_request",
                   return_value=_make_resp({}, status=201)) as mock_req:
            create_gitee_repo("owner", "token", "newrepo", False, long_desc, "user")
            _, kwargs = mock_req.call_args
            assert len(kwargs["json"]["description"]) <= 200

    def test_org_uses_org_endpoint(self):
        with patch("lib.gitee_api.api_request",
                   return_value=_make_resp({}, status=201)) as mock_req:
            create_gitee_repo("myorg", "token", "repo", False, "", "org")
            url = mock_req.call_args[0][1]
            assert "/orgs/myorg/repos" in url

    def test_bearer_header_in_request(self):
        with patch("lib.gitee_api.api_request",
                   return_value=_make_resp({}, status=201)) as mock_req:
            create_gitee_repo("owner", "mytoken", "repo", False, "", "user")
            _, kwargs = mock_req.call_args
            assert kwargs["headers"]["Authorization"] == "Bearer mytoken"


# ===========================================================================
# get_gitee_repo_details
# ===========================================================================

class TestGetGiteeRepoDetails:
    def test_returns_description_and_homepage(self):
        data = {"description": "Gitee repo", "homepage": "https://example.com"}
        with patch("lib.gitee_api.api_request", return_value=_make_resp(data)):
            result = get_gitee_repo_details("owner", "token", "repo")
        assert result["description"] == "Gitee repo"
        assert result["homepage"] == "https://example.com"

    def test_returns_none_on_non_200(self):
        with patch("lib.gitee_api.api_request",
                   return_value=_make_resp({}, status=404)):
            result = get_gitee_repo_details("owner", "token", "repo")
        assert result is None

    def test_normalizes_none_to_empty_string(self):
        data = {"description": None, "homepage": None}
        with patch("lib.gitee_api.api_request", return_value=_make_resp(data)):
            result = get_gitee_repo_details("owner", "token", "repo")
        assert result["description"] == ""
        assert result["homepage"] == ""


# ===========================================================================
# update_gitee_repo_metadata
# ===========================================================================

class TestUpdateGiteeRepoMetadata:
    def test_returns_true_on_success(self):
        with patch("lib.gitee_api.api_request",
                   return_value=_make_resp({}, status=200)):
            ok = update_gitee_repo_metadata("owner", "token", "repo",
                                            {"description": "new"})
        assert ok is True

    def test_returns_false_on_failure(self):
        with patch("lib.gitee_api.api_request",
                   return_value=_make_resp({}, status=422)):
            ok = update_gitee_repo_metadata("owner", "token", "repo",
                                            {"description": "new"})
        assert ok is False

    def test_bearer_header_in_request(self):
        with patch("lib.gitee_api.api_request",
                   return_value=_make_resp({}, status=200)) as mock_req:
            update_gitee_repo_metadata("owner", "mytoken", "repo",
                                       {"description": "updated"})
            _, kwargs = mock_req.call_args
            assert kwargs["headers"]["Authorization"] == "Bearer mytoken"
            assert kwargs["json"]["description"] == "updated"
