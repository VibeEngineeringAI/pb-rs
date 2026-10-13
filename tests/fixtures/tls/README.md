These files contain a test-only CA, localhost/fixture.test server certificate,
and PKCS#8 server key in DER format. They were generated with OpenSSL for native
transport tests and are not credentials for any external service. The leaf was
issued on 2026-10-12 with a ten-year validity period. Tests inject the CA directly
into a private Rustls root store; the system trust store is never changed.
