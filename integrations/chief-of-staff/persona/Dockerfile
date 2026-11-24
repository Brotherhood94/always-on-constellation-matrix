FROM rust:1.92-bookworm AS build
RUN apt-get update && apt-get install -y --no-install-recommends libpam0g-dev libclang-dev cmake && rm -rf /var/lib/apt/lists/*
RUN git init /source && git -C /source fetch --depth 1 https://github.com/microsoft/amplifier-persona-workshop.git df656e57d4870a51eab76d19bad8311c274bec86 && git -C /source checkout --detach FETCH_HEAD
WORKDIR /source
ENV CARGO_NET_GIT_FETCH_WITH_CLI=true
RUN --mount=type=cache,target=/usr/local/cargo/registry --mount=type=cache,target=/usr/local/cargo/git cargo build --release --locked --jobs 4 --bin persona-workshop

FROM debian:bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends libpam0g ca-certificates && rm -rf /var/lib/apt/lists/* && groupadd --gid 991 chief-of-staff && useradd --uid 991 --gid 991 --no-create-home chief-of-staff && install -d -m 0700 -o 991 -g 991 /data
COPY --from=build /source/target/release/persona-workshop /usr/local/bin/persona-workshop
COPY --chmod=0755 start-workshop /usr/local/bin/start-workshop
USER 991:991
ENTRYPOINT ["/usr/local/bin/start-workshop"]
