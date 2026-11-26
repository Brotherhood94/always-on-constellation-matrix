# syntax=docker/dockerfile:1
ARG MAUTRIX_IMAGE=dock.mau.dev/mautrix/telegram:v26.11@sha256:fe78dbce96dca82f077371f429776ab1fee3b65ddc83924a257279b142160945
FROM golang:1.27.4-alpine3.24@sha256:70bd7ddea8b6cbe5d6943cde279d6b67feb10cc924fb9100d1d8757c1575b280 AS build
RUN apk add --no-cache git ca-certificates build-base olm-dev patch
WORKDIR /bridge
RUN git init -q \
    && git fetch -q --depth=1 https://github.com/mautrix/telegram.git 8d85e547208fcdeae47b08f57bca0a7c8cf910af \
    && git checkout -q --detach FETCH_HEAD
COPY chat-link.patch chat-link_test.go office-portal.patch contact-search.patch contact-search_test.go /tmp/
RUN --mount=type=cache,target=/go/pkg/mod --mount=type=cache,target=/root/.cache/go-build \
    go mod download maunium.net/go/mautrix \
    && cp -r "$(go env GOMODCACHE)/maunium.net/go/mautrix@v0.33.0" /mautrix \
    && chmod -R u+w /mautrix \
    && patch -d /mautrix -p1 < /tmp/chat-link.patch \
    && patch -d /mautrix -p1 < /tmp/office-portal.patch \
    && patch -p1 < /tmp/contact-search.patch \
    && cp /tmp/chat-link_test.go /mautrix/bridgev2/commands/ \
    && cp /tmp/contact-search_test.go pkg/connector/ \
    && go mod edit -replace=maunium.net/go/mautrix=/mautrix \
    && go test maunium.net/go/mautrix/bridgev2/commands -run '^TestOpenedChatLink$' \
    && GOFLAGS='-p=2 -gcflags=go.mau.fi/mautrix-telegram/pkg/gotd/tg=-l' go test ./pkg/connector -run '^TestMatchingSavedContacts$' \
    && GOFLAGS='-p=2 -gcflags=go.mau.fi/mautrix-telegram/pkg/gotd/tg=-l' ./build.sh

FROM ${MAUTRIX_IMAGE}
RUN apk add --no-cache olm
COPY --from=build /bridge/mautrix-telegram /usr/bin/mautrix-telegram
RUN /usr/bin/mautrix-telegram --version
