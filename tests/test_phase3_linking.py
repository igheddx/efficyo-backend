from app.services.aws_extended_inventory import _lambda_arn_from_integration_uri, _link_cloudfront_to_acm
from app.services.detection_service import _vpc_link_status


def test_cloudfront_acm_direct_arn_link():
    batches = {
        "cloudfront_distribution": [
            {
                "resource_id": "DIST1",
                "configuration_json": {
                    "domain_name": "d111111abcdef8.cloudfront.net",
                    "aliases": ["api.example.com"],
                    "viewer_certificate_acm_arn": "arn:aws:acm:us-east-1:123:certificate/abc",
                    "linked_resources": [],
                },
            }
        ],
        "acm_certificate": [
            {
                "resource_id": "arn:aws:acm:us-east-1:123:certificate/abc",
                "configuration_json": {
                    "domain_name": "api.example.com",
                    "subject_alternative_names": ["www.example.com"],
                    "status": "ISSUED",
                    "linked_resources": [],
                },
            }
        ],
    }
    _link_cloudfront_to_acm(batches)
    cf_cfg = batches["cloudfront_distribution"][0]["configuration_json"]
    assert cf_cfg["linked_acm_certificate_arn"] == "arn:aws:acm:us-east-1:123:certificate/abc"
    assert cf_cfg["link_confidence"] == "direct_arn_match"
    assert len(cf_cfg["linked_resources"]) == 1


def test_cloudfront_acm_domain_ambiguous_no_false_positive():
    batches = {
        "cloudfront_distribution": [
            {
                "resource_id": "DIST1",
                "configuration_json": {
                    "domain_name": "d111111abcdef8.cloudfront.net",
                    "aliases": ["api.example.com"],
                    "linked_resources": [],
                },
            }
        ],
        "acm_certificate": [
            {
                "resource_id": "arn:aws:acm:us-east-1:123:certificate/abc",
                "configuration_json": {"domain_name": "api.example.com", "subject_alternative_names": [], "linked_resources": []},
            },
            {
                "resource_id": "arn:aws:acm:us-east-1:123:certificate/def",
                "configuration_json": {"domain_name": "api.example.com", "subject_alternative_names": [], "linked_resources": []},
            },
        ],
    }
    _link_cloudfront_to_acm(batches)
    cf_cfg = batches["cloudfront_distribution"][0]["configuration_json"]
    assert cf_cfg["link_confidence"] == "unknown"
    assert "linked_acm_certificate_arn" not in cf_cfg


def test_vpc_link_status_states():
    assert _vpc_link_status("", [], [], []) == "not_attached"
    assert _vpc_link_status("vpc-1", ["subnet-1"], ["sg-1"], []) == "attached_missing_snapshots"
    assert (
        _vpc_link_status(
            "vpc-1",
            ["subnet-1"],
            ["sg-1"],
            [{"resource_type": "vpc"}, {"resource_type": "subnet"}, {"resource_type": "security_group"}],
        )
        == "attached_and_linked"
    )


def test_lambda_arn_from_apigw_integration_uri():
    uri = (
        "arn:aws:apigateway:us-east-1:lambda:path/2015-03-31/functions/"
        "arn:aws:lambda:us-east-1:123456789012:function:my-fn/invocations"
    )
    assert _lambda_arn_from_integration_uri(uri) == "arn:aws:lambda:us-east-1:123456789012:function:my-fn"


def test_lambda_arn_from_integration_uri_non_lambda_returns_none():
    assert _lambda_arn_from_integration_uri("https://example.com/non-lambda") is None
