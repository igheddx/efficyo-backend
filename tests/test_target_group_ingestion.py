from app.services import aws_extended_inventory


class _Paginator:
    def __init__(self, pages):
        self._pages = pages

    def paginate(self, **_kwargs):
        return self._pages


class _FakeElbv2:
    def get_paginator(self, name):
        if name == "describe_target_groups":
            return _Paginator(
                [
                    {
                        "TargetGroups": [
                            {
                                "TargetGroupArn": "arn:aws:elasticloadbalancing:us-east-1:123:targetgroup/tg-a/abc",
                                "TargetGroupName": "tg-a",
                                "Protocol": "HTTP",
                                "Port": 80,
                                "VpcId": "vpc-1",
                                "TargetType": "instance",
                                "HealthCheckEnabled": True,
                                "HealthCheckProtocol": "HTTP",
                                "HealthCheckPath": "/health",
                                "HealthCheckIntervalSeconds": 30,
                                "HealthCheckTimeoutSeconds": 5,
                                "HealthyThresholdCount": 5,
                                "UnhealthyThresholdCount": 2,
                                "Matcher": {"HttpCode": "200"},
                                "LoadBalancerArns": [
                                    "arn:aws:elasticloadbalancing:us-east-1:123:loadbalancer/app/lb-a/xyz"
                                ],
                            }
                        ]
                    }
                ]
            )
        raise AssertionError(f"unexpected paginator {name}")

    def describe_target_health(self, TargetGroupArn):  # noqa: N803
        assert "targetgroup/tg-a" in TargetGroupArn
        return {
            "TargetHealthDescriptions": [
                {
                    "Target": {"Id": "i-1", "Port": 80, "AvailabilityZone": "us-east-1a"},
                    "TargetHealth": {"State": "healthy", "Reason": ""},
                },
                {
                    "Target": {"Id": "i-2", "Port": 80, "AvailabilityZone": "us-east-1b"},
                    "TargetHealth": {"State": "unhealthy", "Reason": "Target.Timeout"},
                },
            ]
        }

    def describe_target_group_attributes(self, TargetGroupArn):  # noqa: N803
        assert "targetgroup/tg-a" in TargetGroupArn
        return {
            "Attributes": [
                {"Key": "deregistration_delay.timeout_seconds", "Value": "45"},
                {"Key": "stickiness.enabled", "Value": "true"},
                {"Key": "stickiness.type", "Value": "lb_cookie"},
            ]
        }

    def describe_tags(self, ResourceArns):  # noqa: N803
        assert len(ResourceArns) == 1
        return {
            "TagDescriptions": [
                {
                    "ResourceArn": ResourceArns[0],
                    "Tags": [
                        {"Key": "Name", "Value": "tg-a"},
                        {"Key": "Environment", "Value": "test"},
                    ],
                }
            ]
        }


def test_target_group_ingestion_collects_health_and_linkage(monkeypatch):
    monkeypatch.setattr(
        aws_extended_inventory,
        "_create_assumed_client",
        lambda *_args, **_kwargs: _FakeElbv2(),
    )

    rows = aws_extended_inventory._target_groups_one_region(
        "arn:aws:iam::123:role/test",
        "us-east-1",
        None,
    )

    assert len(rows) == 1
    cfg = rows[0]["configuration_json"]
    assert cfg["target_group_arn"].endswith("targetgroup/tg-a/abc")
    assert cfg["load_balancer_arn"].endswith("loadbalancer/app/lb-a/xyz")
    assert cfg["total_targets"] == 2
    assert cfg["healthy_count"] == 1
    assert cfg["unhealthy_count"] == 1
    assert cfg["target_health_states"]["healthy"] == 1
    assert cfg["target_health_states"]["unhealthy"] == 1
    assert cfg["health_check_timeout_seconds"] == 5
    assert rows[0]["tags_json"]["Environment"] == "test"
