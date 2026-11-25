import ctypes
import ssl
import sys
from ctypes import wintypes

SERVER_AUTH = b"1.3.6.1.5.5.7.3.1"
X509_ASN_ENCODING = 0x00000001
PKCS_7_ASN_ENCODING = 0x00010000
USAGE_MATCH_TYPE_AND = 0


class _EnhancedKeyUsage(ctypes.Structure):
    _fields_ = [("count", wintypes.DWORD),
                ("oids", ctypes.POINTER(ctypes.c_char_p))]


class _UsageMatch(ctypes.Structure):
    _fields_ = [("kind", wintypes.DWORD), ("usage", _EnhancedKeyUsage)]


class _ChainPara(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD),
                ("requested_usage", _UsageMatch),
                ("requested_issuance_policy", _UsageMatch),
                ("url_retrieval_timeout", wintypes.DWORD),
                ("check_revocation_freshness", wintypes.BOOL),
                ("revocation_freshness_time", wintypes.DWORD),
                ("oid", wintypes.LPCWSTR)]


class _TrustStatus(ctypes.Structure):
    _fields_ = [("error", wintypes.DWORD), ("confidence", wintypes.DWORD)]


class _CertContext(ctypes.Structure):
    _fields_ = [("encoding", wintypes.DWORD),
                ("encoded", ctypes.POINTER(ctypes.c_ubyte)),
                ("length", wintypes.DWORD),
                ("info", ctypes.c_void_p),
                ("store", ctypes.c_void_p)]


class _ChainElement(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD),
                ("cert", ctypes.POINTER(_CertContext)),
                ("trust", _TrustStatus),
                ("extra", ctypes.c_void_p),
                ("message", wintypes.LPCWSTR)]


class _SimpleChain(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD),
                ("trust", _TrustStatus),
                ("count", wintypes.DWORD),
                ("elements", ctypes.POINTER(ctypes.POINTER(_ChainElement))),
                ("trust_list", ctypes.c_void_p),
                ("has_revocation_freshness", wintypes.BOOL),
                ("revocation_freshness_time", wintypes.DWORD)]


class _ChainContext(ctypes.Structure):
    _fields_ = [("size", wintypes.DWORD),
                ("trust", _TrustStatus),
                ("count", wintypes.DWORD),
                ("chains", ctypes.POINTER(ctypes.POINTER(_SimpleChain))),
                ("lower_count", wintypes.DWORD),
                ("lower_chains", ctypes.c_void_p),
                ("has_revocation_freshness", wintypes.BOOL),
                ("revocation_freshness_time", wintypes.DWORD),
                ("flags", wintypes.DWORD)]


def available():
    return sys.platform == "win32"


def _library():
    if not available():
        raise ValueError("цепочку сертификата достраивает только Windows")
    library = ctypes.WinDLL("crypt32", use_last_error=True)
    library.CertCreateCertificateContext.argtypes = [
        wintypes.DWORD, ctypes.c_char_p, wintypes.DWORD]
    library.CertCreateCertificateContext.restype = ctypes.POINTER(_CertContext)
    library.CertGetCertificateChain.argtypes = [
        ctypes.c_void_p, ctypes.POINTER(_CertContext), ctypes.c_void_p,
        ctypes.c_void_p, ctypes.POINTER(_ChainPara), wintypes.DWORD,
        ctypes.c_void_p, ctypes.POINTER(ctypes.POINTER(_ChainContext))]
    library.CertGetCertificateChain.restype = wintypes.BOOL
    library.CertFreeCertificateChain.argtypes = [
        ctypes.POINTER(_ChainContext)]
    library.CertFreeCertificateContext.argtypes = [
        ctypes.POINTER(_CertContext)]
    library.CertFreeCertificateContext.restype = wintypes.BOOL
    return library


def _parameters(usage):
    match = _UsageMatch(kind=USAGE_MATCH_TYPE_AND,
                        usage=_EnhancedKeyUsage(count=1, oids=usage))
    return _ChainPara(size=ctypes.sizeof(_ChainPara), requested_usage=match,
                      requested_issuance_policy=_UsageMatch())


def pems(der):
    library = _library()
    usage = (ctypes.c_char_p * 1)(SERVER_AUTH)
    context = library.CertCreateCertificateContext(
        X509_ASN_ENCODING | PKCS_7_ASN_ENCODING, der, len(der))
    if not context:
        raise ValueError("сертификат источника не разбирается")
    chain = ctypes.POINTER(_ChainContext)()
    try:
        built = library.CertGetCertificateChain(
            None, context, None, None, ctypes.byref(_parameters(usage)), 0,
            None, ctypes.byref(chain))
        if not built:
            code = ctypes.get_last_error()
            raise ValueError("Windows не собрала цепочку сертификата"
                             f" (код {code})")
        error = chain.contents.trust.error
        if error:
            raise ValueError(
                f"сертификат источника не подтверждён Windows (0x{error:08X})")
        simple = chain.contents.chains[0].contents
        result = []
        for index in range(1, simple.count):
            cert = simple.elements[index].contents.cert.contents
            result.append(ssl.DER_cert_to_PEM_cert(
                ctypes.string_at(cert.encoded, cert.length)))
        return result
    finally:
        if chain:
            library.CertFreeCertificateChain(chain)
        library.CertFreeCertificateContext(context)
