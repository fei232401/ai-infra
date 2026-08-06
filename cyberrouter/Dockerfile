# CyberRouter Operator 镜像 — 多阶段构建
# 最终镜像用 distroless (无 shell, 最小攻击面), 面试可讲
FROM golang:1.26 AS builder
WORKDIR /workspace
COPY go.mod go.sum ./
RUN go mod download
COPY . .
RUN CGO_ENABLED=0 GOOS=linux go build -o manager .

FROM gcr.io/distroless/static:nonroot
COPY --from=builder /workspace/manager .
# 运行非 root: distroless nonroot 默认 uid 65532
USER 65532:65532
ENTRYPOINT ["/manager"]
