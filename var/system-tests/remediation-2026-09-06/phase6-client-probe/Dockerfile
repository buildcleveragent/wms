FROM mysql:8.0.46@sha256:7dcddc01f13bab2f15cde676d44d01f61fc9f99fe7785e86196dfc07d358ae2b AS mysql_client
FROM wms-system-test:remediation-test-v6
USER root
COPY --from=mysql_client /usr/bin/mysql /usr/bin/mysqldump /usr/local/bin/
RUN python /app/scripts/verify_runtime_dependencies.py snapshot /tmp/before-client-tools.json \
    && apt-get -o Acquire::Retries=2 -o Acquire::https::Timeout=30 update \
    && apt-get install -y --no-install-recommends libncurses6 \
    && rm -rf /var/lib/apt/lists/* \
    && mysql --version && mysqldump --version \
    && python /app/scripts/verify_runtime_dependencies.py verify /tmp/before-client-tools.json
USER wms
