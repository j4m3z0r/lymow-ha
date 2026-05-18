"""SigV4 request signing using botocore (bundled with boto3)."""
from __future__ import annotations

import botocore.auth
import botocore.awsrequest
import botocore.credentials


def sign_request(
    method: str,
    url: str,
    credentials: dict,
    region: str,
    service: str,
    body: bytes = b"",
) -> dict[str, str]:
    """Return a dict of signed HTTP headers for the given request."""
    creds = botocore.credentials.Credentials(
        access_key=credentials["AccessKeyId"],
        secret_key=credentials["SecretAccessKey"],
        token=credentials["SessionToken"],
    )
    request = botocore.awsrequest.AWSRequest(method=method.upper(), url=url, data=body)
    signer = botocore.auth.SigV4Auth(creds, service, region)
    signer.add_auth(request)
    return dict(request.headers)
