FROM ghcr.io/fatedier/frpc:v0.72.0@sha256:c447f0c3572a02bd3da5677f249db3b62cb02d7367d1d5bb420678ce092d65c8 AS frp
FROM python:3.13-alpine@sha256:1e1f6334529d1fb26a2ef619d7cffb30a7c7d7cc1cc7338ebee283a58d1aeaca AS python-runtime

# Copy only the runtime used by the helper. Shells, package managers, pip,
# readline and database/compression libraries are not part of the final image.
RUN mkdir -p /runtime/usr/local/bin /runtime/usr/local/lib /runtime/lib /runtime/usr/lib /runtime/etc/ssl/certs /runtime/data /runtime/tmp \
    && cp /usr/local/bin/python3.13 /runtime/usr/local/bin/ \
    && ln -s python3.13 /runtime/usr/local/bin/python \
    && cp -a /usr/local/lib/libpython3.13.so* /runtime/usr/local/lib/ \
    && cp -a /usr/local/lib/python3.13 /runtime/usr/local/lib/ \
    && rm -rf /runtime/usr/local/lib/python3.13/site-packages /runtime/usr/local/lib/python3.13/ensurepip /runtime/usr/local/lib/python3.13/idlelib /runtime/usr/local/lib/python3.13/tkinter /runtime/usr/local/lib/python3.13/test \
    && rm -f /runtime/usr/local/lib/python3.13/lib-dynload/_bz2*.so /runtime/usr/local/lib/python3.13/lib-dynload/_ctypes*.so /runtime/usr/local/lib/python3.13/lib-dynload/_curses*.so /runtime/usr/local/lib/python3.13/lib-dynload/_dbm*.so /runtime/usr/local/lib/python3.13/lib-dynload/_gdbm*.so /runtime/usr/local/lib/python3.13/lib-dynload/_lzma*.so /runtime/usr/local/lib/python3.13/lib-dynload/_sqlite3*.so /runtime/usr/local/lib/python3.13/lib-dynload/_uuid*.so /runtime/usr/local/lib/python3.13/lib-dynload/readline*.so /runtime/usr/local/lib/python3.13/lib-dynload/_test*.so \
    && cp -a /lib/ld-musl-*.so.1 /runtime/lib/ \
    && cp -a /usr/lib/libssl.so.3 /usr/lib/libcrypto.so.3 /usr/lib/libz.so* /runtime/usr/lib/ \
    && cp /etc/ssl/certs/ca-certificates.crt /runtime/etc/ssl/certs/ \
    && chmod 1777 /runtime/tmp && chown 65534:65534 /runtime/data

FROM scratch

LABEL org.opencontainers.image.title="frp-tunnel-helper" \
      org.opencontainers.image.description="Persistent HTTP share URLs for an existing self-hosted frp relay" \
      org.opencontainers.image.version="0.3.0" \
      org.opencontainers.image.licenses="MIT" \
      org.opencontainers.image.source="https://github.com/oiBio/frp-tunnel-helper" \
      org.opencontainers.image.url="https://hub.docker.com/r/oibio/frp-tunnel-helper"
COPY --from=python-runtime /runtime/ /
COPY --from=frp /usr/bin/frpc /usr/local/bin/frpc
COPY --chmod=755 src/tunnel_helper.py /usr/local/bin/tunnel-helper
COPY LICENSE THIRD_PARTY_NOTICES.md /usr/share/licenses/frp-tunnel-helper/
COPY licenses/ /usr/share/licenses/frp-tunnel-helper/third-party/
ENV PATH=/usr/local/bin PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
USER 65534:65534
ENTRYPOINT ["/usr/local/bin/python", "/usr/local/bin/tunnel-helper"]
