import pytest

from tunnelvpn import winchain


def test_чужой_сертификат_не_разбирается():
    with pytest.raises(ValueError, match="не разбирается"):
        winchain.pems(b"not a certificate")
