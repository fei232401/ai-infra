# CyberRouter Operator 镜像 — 多阶段构建
# 注: 不用 distroless (gcr.io 被墙拉取超时), 用 alpine (docker.io daocloud 加速可拉)
# 含 CA 证书 (k8s client in-cluster TLS 需要), 非 root 运行
FROM golang:1.26 AS builder
WORKDIR /workspace
# China 网络: proxy.golang.org 被墙, 用 goproxy.cn
ENV GOPROXY=https://goproxy.cn,direct
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o manager .

FROM alpine:3.20
RUN addgroup -S -g 65532 app && adduser -S -u 65532 -G app app
COPY --from=builder /workspace/manager /manager
# 非 root 最小权限
USER 65532
ENTRYPOINT ["/manager"]
