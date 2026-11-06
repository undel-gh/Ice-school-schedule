FROM alpine:3.23

RUN apk add --no-cache restic ca-certificates
WORKDIR /ops
COPY ops/offsite_backup.sh ops/offsite_healthcheck.sh /ops/
RUN chmod 755 /ops/offsite_backup.sh /ops/offsite_healthcheck.sh

# The existing dumps are root:root 0600. Mount their directory read-only;
# drop all capabilities in Compose and do not expose network ports.
ENTRYPOINT ["/bin/sh", "/ops/offsite_backup.sh"]
