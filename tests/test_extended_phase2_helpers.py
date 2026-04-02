from app.services.aws_extended_inventory import _summarize_world_open_ports
from app.services.detection_extended_service import _is_outdated_lambda_runtime


def test_summarize_world_open_ports_detects_sensitive_ranges():
    permissions = [
        {
            "IpProtocol": "tcp",
            "FromPort": 22,
            "ToPort": 22,
            "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
            "Ipv6Ranges": [],
        },
        {
            "IpProtocol": "tcp",
            "FromPort": 3389,
            "ToPort": 3389,
            "IpRanges": [],
            "Ipv6Ranges": [{"CidrIpv6": "::/0"}],
        },
        {
            "IpProtocol": "-1",
            "IpRanges": [{"CidrIp": "0.0.0.0/0"}],
            "Ipv6Ranges": [],
        },
    ]
    assert _summarize_world_open_ports(permissions) == (True, True, True)


def test_is_outdated_lambda_runtime_flags_deprecated_versions():
    assert _is_outdated_lambda_runtime("python3.7") is True
    assert _is_outdated_lambda_runtime("nodejs14.x") is True
    assert _is_outdated_lambda_runtime("python3.11") is False
    assert _is_outdated_lambda_runtime("nodejs20.x") is False
