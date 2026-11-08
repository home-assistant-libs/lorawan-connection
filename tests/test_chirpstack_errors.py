"""ChirpStack transport error classification."""

import asyncio
from unittest.mock import AsyncMock, Mock

import grpc
import pytest
from chirpstack_api import api

from lorawan_connection import ConnectionUnavailable
from lorawan_connection.backend.chirpstack import (
    AuthenticationError,
    ChirpStackConnection,
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


@pytest.fixture
def connection() -> ChirpStackConnection:
    connection = ChirpStackConnection(
        "http://localhost:8080",
        "secret",
        tenant_id="tenant",
        application_ids=["app"],
        network_id="network",
        channel=Mock(close=AsyncMock()),
    )
    connection._tenant_api.Get = AsyncMock()
    connection._tenant_api.List = AsyncMock()
    connection._application_api.List = AsyncMock(
        return_value=api.ListApplicationsResponse(
            total_count=1, result=[api.ApplicationListItem(id="app")]
        )
    )
    connection._device_api.List = AsyncMock(
        return_value=api.ListDevicesResponse(
            total_count=1,
            result=[
                api.DeviceListItem(
                    dev_eui="0102030405060708", device_profile_id="profile"
                )
            ],
        )
    )
    connection._profile_api.Get = AsyncMock(
        return_value=api.GetDeviceProfileResponse(
            device_profile=api.DeviceProfile(device_id="catalog")
        )
    )
    connection._profile_api.GetDevice = AsyncMock(
        return_value=api.GetDeviceProfileDeviceResponse(
            device=api.DeviceProfileDevice(vendor_id="vendor")
        )
    )
    connection._profile_api.GetVendor = AsyncMock()
    return connection


@pytest.mark.parametrize(
    ("method", "service", "rpc"),
    [
        ("tenants", "_tenant_api", "List"),
        ("applications", "_tenant_api", "Get"),
        ("applications", "_application_api", "List"),
        ("inventory", "_device_api", "List"),
        ("inventory", "_profile_api", "Get"),
        ("inventory", "_profile_api", "GetDevice"),
        ("inventory", "_profile_api", "GetVendor"),
        ("refresh", "_device_api", "List"),
        ("async_connect", "_device_api", "List"),
    ],
)
@pytest.mark.parametrize(
    ("code", "details", "expected"),
    [
        (grpc.StatusCode.UNAUTHENTICATED, "bad key", AuthenticationError),
        (grpc.StatusCode.PERMISSION_DENIED, "scope denied", AuthenticationError),
        (grpc.StatusCode.UNAVAILABLE, "offline", ConnectionUnavailable),
        (grpc.StatusCode.UNAVAILABLE, "missing selected ALPN property", HTTP2Error),
        (grpc.StatusCode.UNAVAILABLE, "CERTIFICATE_VERIFY_FAILED", TLSCertificateError),
    ],
)
async def test_public_errors(
    connection: ChirpStackConnection,
    method: str,
    service: str,
    rpc: str,
    code: grpc.StatusCode,
    details: str,
    expected: type[Exception],
) -> None:
    """Public methods classify RPC failures and preserve the original cause."""
    error = grpc.aio.AioRpcError(code, (), (), details)
    getattr(getattr(connection, service), rpc).side_effect = error
    try:
        with pytest.raises(expected) as raised:
            await getattr(connection, method)()
        assert type(raised.value) is expected
        assert raised.value.__cause__ is error
        assert details in str(raised.value)
    finally:
        await connection.close()


@pytest.mark.parametrize("rpc", ["GetDevice", "GetVendor"])
async def test_missing_catalog_is_optional(
    connection: ChirpStackConnection, rpc: str
) -> None:
    """A missing catalog record does not prevent device discovery."""
    getattr(connection._profile_api, rpc).side_effect = grpc.aio.AioRpcError(
        grpc.StatusCode.NOT_FOUND, (), (), "unknown catalog entry"
    )
    try:
        devices = await connection.inventory()
        assert len(devices) == 1
        assert devices[0].dev_eui == "0102030405060708"
        assert devices[0].brand_id is None
    finally:
        await connection.close()


@pytest.mark.parametrize(
    "error", [ValueError("invalid data"), asyncio.CancelledError()]
)
async def test_non_rpc_errors_propagate(
    connection: ChirpStackConnection, error: BaseException
) -> None:
    """Cancellation and programming errors retain their original identity."""
    connection._tenant_api.List.side_effect = error
    try:
        with pytest.raises(type(error)) as raised:
            await connection.tenants()
        assert raised.value is error
    finally:
        await connection.close()
