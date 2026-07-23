# M2 Wallet 内部部署说明

## 1. 部署模式

建议分两个阶段：

1. **内部演示**：单机、SQLite、模拟地址/签名/广播，只允许本机访问。
2. **测试链联调**：独立地址服务、独立签名服务、TRON/Polygon RPC 和四个后台任务。

主网不属于当前 Demo 验收范围。切换主网前必须另行完成密钥托管、限额、双人复核、监控和灾备评审。

## 2. 最快本机启动

```bash
cp .env.example .env
python3 -m backend.seed_demo
python3 -m backend.server
```

访问 `http://127.0.0.1:8787`。演示完成后按 `Ctrl+C` 停止。

必须修改 `.env` 中的 `M2_WALLET_API_KEY` 和 `M2_CALLBACK_SECRET`。`.env` 已被 Git 忽略。
同时必须替换 `M2_ADMIN_PASSWORD`、`M2_FINANCE_PASSWORD`、
`M2_OPERATOR_PASSWORD` 和 `M2_VIEWER_PASSWORD`；服务绑定非本机地址时会拒绝使用默认凭据启动。

## 3. Docker 内部部署

```bash
cp .env.example .env
docker compose build
docker compose up -d m2-wallet
docker compose exec m2-wallet python -m backend.seed_demo
```

Compose 会让容器内服务监听 `0.0.0.0`，但宿主机端口仍只映射到
`127.0.0.1:8787`，不会直接暴露给外部网络。

健康检查：

```bash
curl http://127.0.0.1:8787/api/v1/health
```

默认只绑定服务器的 `127.0.0.1`。如需团队访问，应通过公司 VPN 或带身份认证的反向代理开放，不能直接暴露到公网。

## 4. 测试链工作进程

先配置 `.env`：

- `M2_ADDRESS_PROVIDER_URL/TOKEN`：外部钱包地址服务
- `M2_SIGNER_URL/TOKEN`：独立签名服务或 HSM 网关
- `M2_TRONGRID_API_KEY` 与 `M2_TRON_BROADCAST_URL`
- `M2_POLYGON_RPC_URL`
- `M2_CALLBACK_SECRET` 与允许的商户回调域名
- `M2_BROADCAST_MODE=rpc`
- `M2_PAYOUTS_ENABLED=true` 与合适的 `M2_MAX_WITHDRAWAL_AMOUNT`

然后启动：

```bash
docker compose --profile chain-workers up -d
```

后台任务分别负责 TRON 收款、Polygon 收款、提现确认和商户回调。广播成功的提现保持 `BROADCASTED`，达到确认数后才进入 `CONFIRMED` 并写账。

## 5. 验收检查

```bash
python3 -m unittest -v tests.test_backend
python3 -m backend.reconcile
```

验收要求：

- 测试全部通过
- `/api/v1/reconciliation` 返回 `ok: true`
- 借贷分录平衡
- 重复业务单号不会重复出款或重复记账
- 财务拒绝的提现不产生签名或链上交易
- 运营角色不能审批，财务角色不能创建订单，只读角色不能执行写操作
- 审批人由登录会话确定，审批和风控修改均出现在审计日志
- 回调失败达到上限后进入 `FAILED`，可由运营页面重新入队
- 真实广播模式下，广播与确认状态严格分离

## 6. 备份与恢复

创建一致性备份：

```bash
python3 -m backend.backup --output-dir /secure/backup/path
```

恢复时先停止所有 M2 Wallet 服务，再用验证过的备份替换 `data/m2-wallet.db`。保留原数据库副本，启动后立即运行对账命令。

## 7. 安全边界

- M2 Wallet 应用不得接收或保存助记词、原始私钥、HSM 主密钥。
- 地址服务、签名服务和应用使用不同凭据与网络权限。
- API、RPC、回调密钥只放入密钥管理系统或 `.env`，不得提交 Git。
- 对外回调只允许 HTTPS 和明确域名白名单，防止服务器请求伪造。
- 当前实现了单笔限额、每日累计限额、地址白名单和总暂停开关；商业化版本仍需增加可配置的大额双人复核。
- 数据库及备份目录只能由服务账号读取。

## 8. 回滚

1. 停止链监听和确认工作进程，避免状态继续变化。
2. 停止 API 服务并备份当前数据库。
3. 恢复上一版本镜像和与之匹配的数据库备份。
4. 先运行对账，再启动 API；最后恢复监听任务。
