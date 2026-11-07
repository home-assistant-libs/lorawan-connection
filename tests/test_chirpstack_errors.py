"""ChirpStack transport error classification."""

import grpc
import pytest

from lorawan_connection import ConnectionUnavailable
from lorawan_connection.backend.chirpstack import (
    AuthenticationError,
    HTTP2Error,
    TLSCertificateError,
    connection_error,
)


@pytest.mark.parametrize(
    ("details", "expected"),
    [
        pytest.param(
            "failed to connect to all addresses; last error: UNKNOWN: "
            "Cannot check peer: missing selected ALPN property.",
            HTTP2Error,
            id="https-without-http2",
        ),
        pytest.param(
            "Failed parsing HTTP/2 (Expected SETTINGS frame as the first frame, "
            "got frame type 80)",
            HTTP2Error,
            id="http1-response",
        ),
        pytest.param(
            "Ssl handshake failed: SSL_ERROR_SSL: error:1000007d:SSL routines:"
            "OPENSSL_internal:CERTIFICATE_VERIFY_FAILED: self signed certificate",
            TLSCertificateError,
            id="untrusted-certificate",
        ),
        pytest.param(
            "Peer name chirpstack.local is not in peer certificate",
            TLSCertificateError,
            id="hostname-mismatch",
        ),
        pytest.param(
            "Custom verification check failed with error: UNAUTHENTICATED: "
            "Hostname Verification Check failed.",
            TLSCertificateError,
            id="hostname-verification",
        ),
        pytest.param("Connection refused", ConnectionUnavailable, id="offline"),
        pytest.param("DNS resolution failed", ConnectionUnavailable, id="dns"),
        pytest.param("Ssl handshake failed", ConnectionUnavailable, id="other-tls"),
        pytest.param(None, ConnectionUnavailable, id="no-details"),
    ],
)
def test_transport_error(details: str | None, expected: type[Exception]) -> None:
    """Identify known failures without guessing from UNAVAILABLE alone."""
    error = grpc.aio.AioRpcError(grpc.StatusCode.UNAVAILABLE, (), (), details)
    failure = connection_error(error)
    assert type(failure) is expected
    assert failure.__cause__ is error
    assert str(failure) == f"ChirpStack RPC UNAVAILABLE: {details}"


@pytest.mark.parametrize(
    ("code", "expected"),
    [
        (grpc.StatusCode.UNAUTHENTICATED, AuthenticationError),
        (grpc.StatusCode.PERMISSION_DENIED, AuthenticationError),
        (grpc.StatusCode.DEADLINE_EXCEEDED, ConnectionUnavailable),
        (grpc.StatusCode.UNKNOWN, ConnectionUnavailable),
    ],
)
def test_other_status(code: grpc.StatusCode, expected: type[Exception]) -> None:
    """Transport hints do not override authentication or unrelated statuses."""
    error = grpc.aio.AioRpcError(code, (), (), "Failed parsing HTTP/2")
    assert type(connection_error(error)) is expected


@pytest.mark.parametrize("error_type", [HTTP2Error, TLSCertificateError])
def test_classified_error(error_type: type[ConnectionUnavailable]) -> None:
    """Repeated classification preserves the specific failure."""
    error = error_type("Connection failed")
    assert connection_error(error) is error
