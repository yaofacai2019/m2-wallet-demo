# M2 Wallet

## Browser demo

The browser-local sandbox can be deployed to any static web host. It contains
only simulated balances and transactions, and never touches real digital assets.

- Desktop Chrome: use the install icon in the address bar.
- iPhone/iPad Safari: Share → Add to Home Screen.
- Android Chrome: Install app / Add to Home screen.

M2 Wallet 是面向内部支付团队的稳定币收付与资金运营 Demo。目前支持：

- TRON 网络 USDT 收款订单
- Polygon 网络 USDC 收款订单
- 按订单计算平台佣金
- 客户提现申请与财务审批
- 复式账本及业务回调事件
- 本地模拟链上确认与交易哈希
- 独立签名服务接口及签名、广播、确认状态链
- 带 HMAC 签名的回调、失败重试和异常队列
- 账本与业务状态对账报告

> 默认是模拟环境，不会签名或广播真实资产交易。只有显式接入外部地址服务、
> 独立签名服务和链 RPC 后，才可切换为测试链真实广播模式。

## 本地启动

项目后端只依赖 Python 3.11 或更新版本：

```bash
python3 -m backend.server
```

然后访问：

```text
http://127.0.0.1:8787
```

本地启动时若未设置 `M2_WALLET_API_KEY`，进程会生成临时 API Key 并仅在终端
显示。共享或部署环境必须显式设置 API Key。监听地址和端口可使用
`M2_WALLET_HOST`、`M2_WALLET_PORT` 配置。

浏览器内部试用使用服务端会话和四种角色：`ADMIN`、`FINANCE`、`OPERATOR`、
`VIEWER`。本机 Demo 默认账号显示在登录页；绑定非本机地址前，必须替换
`.env.example` 中列出的全部账号密码和 API Key。

## API

除健康检查外，请求需要携带：

```text
X-API-Key: <your-environment-api-key>
```

主要接口：

- `GET /api/v1/health`
- `GET|POST /api/v1/payment-orders`
- `GET /api/v1/payment-orders/{id-or-merchant-order-id}`
- `GET /api/v1/payment-orders/{id-or-merchant-order-id}/callbacks`
- `POST /api/v1/payment-orders/{id}/simulate-confirm`
- `POST /api/v1/payment-orders/{id}/simulate-expire`
- `GET|POST /api/v1/withdrawals`
- `GET /api/v1/withdrawals/{id-or-merchant-withdraw-id}`
- `GET /api/v1/withdrawals/{id-or-merchant-withdraw-id}/events`
- `GET /api/v1/withdrawals/{id-or-merchant-withdraw-id}/callbacks`
- `POST /api/v1/withdrawals/{id}/approve`
- `POST /api/v1/withdrawals/{id}/reject`
- `GET /api/v1/ledger`
- `GET /api/v1/callbacks`
- `POST /api/v1/callbacks/{id}/retry`
- `POST /api/v1/callbacks/deliver-pending`
- `GET /api/v1/demo-merchant/webhooks`
- `POST /api/v1/demo-merchant/webhook`（本机签名回调接收器）
- `POST /api/v1/demo-merchant/withdrawal-validation`（本机出款前校验器）
- `GET /api/v1/withdrawal-events`
- `GET /api/v1/reconciliation`
- `GET /api/v1/transactions`
- `GET /api/v1/session`
- `POST /api/v1/session`、`POST /api/v1/session/logout`
- `GET /api/v1/audit-logs`
- `GET|POST /api/v1/risk-policy`
- `GET|POST /api/v1/network-reserves`
- `GET /api/v1/network-fee-events`
- `GET|POST /api/v1/collection-policy`
- `GET /api/v1/collection-candidates`
- `GET /api/v1/collections`、`POST /api/v1/collections/run`
- `GET|POST /api/v1/address-book`
- `GET|POST /api/v1/ip-allowlist`
- `GET|POST /api/v1/project-settings`
- `GET /api/v1/demo-readiness`

客户平台字段、状态和回调签名约定见 `INTEGRATION_CONTRACT.md`。

## 链上监听

TRON USDT 监听使用 TronGrid 的已确认 TRC-20 交易接口：

```bash
M2_TRONGRID_API_KEY="..." python3 -m backend.chain_listener --once
```

Polygon USDC 监听使用标准 EVM JSON-RPC。RPC URL 不应提交到 Git：

```bash
M2_POLYGON_RPC_URL="https://your-provider" python3 -m backend.evm_listener --once
```

默认 Polygon USDC 合约地址来自 Circle 官方合约清单；可使用
`M2_POLYGON_USDC_CONTRACT` 覆盖。监听器默认等待 20 个区块确认。

## 回调与对账

回调目标必须使用 HTTPS 并加入允许列表：

```bash
M2_CALLBACK_SECRET="replace-me" \
M2_CALLBACK_ALLOWED_HOSTS="merchant.example.com" \
python3 -m backend.callback_worker --once
```

查看对账报告：

```bash
python3 -m backend.reconcile
```

## 签名服务

默认使用不可花费资产的模拟签名器。配置独立签名服务后，应用只向签名服务发送交易意图：

```bash
M2_SIGNER_URL="https://signer.internal" M2_SIGNER_TOKEN="..." python3 -m backend.server
```

私钥、助记词和 HSM 凭据不得进入 M2 Wallet 应用进程。

真实广播还需要设置 `M2_BROADCAST_MODE=rpc`、链 RPC 地址，并运行
`backend.confirmation_worker`。使用 `M2_PAYOUTS_ENABLED=false` 可立即暂停所有
新出款；`M2_MAX_WITHDRAWAL_AMOUNT` 控制单笔上限。完整启动、备份和回滚步骤见
[DEPLOYMENT.md](DEPLOYMENT.md)。

服务启动后，出款开关与单笔限额会写入数据库，并可由 `ADMIN` 在“风控 → 策略”
中调整。修改会立即生效并进入审计日志。

## 自动测试

```bash
python3 -m unittest -v tests.test_backend
```

测试覆盖 API 鉴权、订单幂等、币种与网络校验、收款确认、提现审批、链上广播、
状态回写、回调重试、对账及复式账本借贷平衡。

## 数据文件

默认 SQLite 数据库位于 `data/m2-wallet.db`，已经配置为不提交到 Git。
测试钱包、助记词、私钥及签名密钥不得写入项目文件或聊天记录。
