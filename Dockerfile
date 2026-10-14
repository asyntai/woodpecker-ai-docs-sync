FROM python:3.13.16-alpine3.24

# The plugin uses only the Python standard library. Nothing else is installed,
# so the image stays small and every build gives the same result.
COPY plugin.py /bin/asyntai-docs-sync
COPY LICENSE docs.md /usr/share/asyntai/
RUN chmod a+x /bin/asyntai-docs-sync

ENTRYPOINT ["python3", "/bin/asyntai-docs-sync"]
