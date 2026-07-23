# M2 Wallet 同事运行与演示手册

## 1. 项目说明

M2 Wallet 是稳定币收付与企业钱包运营 Demo，覆盖：

- USDT-TRON 与 USDC-Polygon 收款订单
- 客户稳定币收银台与订单状态
- 平台佣金、商户净结算和复式账本
- 客户提现申请、财务审批、签名、广播和交易哈希回写
- 支付地址资金归集
- WaaS API、回调、事件时间线与 API Console
- 出款限额、暂停开关、地址黑白名单和审计日志
- Demo Acceptance 实时验收清单

> 当前版本是模拟资金环境，不包含真实助记词、私钥，也不会发送真实数字资产。

## 2. 下载代码

```bash
git clone https://github.com/yaofacai2019/m2-wallet-demo.git
cd m2-wallet-demo
```

## 3. 最快启动方式

电脑需要安装 Python 3.11 或更新版本，不需要安装数据库或其他 Python 依赖。

```bash
python3 -m backend.server
```

浏览器打开：

```text
http://127.0.0.1:8787
```

如果 8787 端口已经被占用：

```bash
M2_WALLET_PORT=8790 python3 -m backend.server
```

然后打开 `http://127.0.0.1:8790`。

## 4. Demo 登录账号

公开版本不附带固定密码。启动前先在当前终端设置本机演示密码和 API 密钥：

```bash
export M2_FINANCE_PASSWORD='choose-a-finance-password'
export M2_OPERATOR_PASSWORD='choose-an-operator-password'
export M2_ADMIN_PASSWORD='choose-an-admin-password'
export M2_VIEWER_PASSWORD='choose-a-viewer-password'
export M2_WALLET_API_KEY='choose-a-local-api-key'
export M2_CALLBACK_SECRET='choose-a-callback-secret'
python3 -m backend.server
```

| 角色 | 用户名 | 密码 | 主要权限 |
| --- | --- | --- | --- |
| 财务 | `finance` | 你设置的 `M2_FINANCE_PASSWORD` | 审批或拒绝提现 |
| 运营 | `operator` | 你设置的 `M2_OPERATOR_PASSWORD` | 创建支付订单和提现申请 |
| 管理员 | `admin` | 你设置的 `M2_ADMIN_PASSWORD` | 风控、API、成员和全部业务权限 |
| 只读 | `viewer` | 你设置的 `M2_VIEWER_PASSWORD` | 只查看数据 |

这些账号只用于本机 Demo。不要把示例密码、API Key 或回调密钥用于公网部署。

## 5. 建议演示顺序

### 场景一：客户使用稳定币付款

1. 使用 `operator` 登录。
2. 打开 `Payment Engine`，进入支付项目。
3. 创建 USDT-TRON 或 USDC-Polygon 订单。
4. 打开订单详情并预览客户收银台。
5. 点击模拟付款，查看到账金额、状态、交易哈希和平台佣金。
6. 打开结算记录，查看商户净结算金额。

### 场景二：客户提现，财务确认后自动发送

1. 使用 `operator` 创建钱包发送或 API 提现申请。
2. 切换到 `finance`。
3. 打开 `Workflow -> Pending approval`。
4. 检查金额、币种、网络、接收地址、策略和客户平台校验结果。
5. 点击 `Approve`。
6. 系统自动模拟签名、广播和确认，并生成交易哈希。

### 场景三：支付地址资金归集

1. 使用 `admin` 或 `operator` 登录。
2. 打开 `Risk Control -> Automation`。
3. 查看达到阈值的 USDT/USDC 可归集资金。
4. 点击立即归集。
5. 查看归集任务、目标钱包、交易哈希和内部账本记录。

### 场景四：客户平台 API 接入

1. 打开 `WaaS Projects -> Settings -> API Console`。
2. 运行 USDT/USDC 收款样例。
3. 运行 USDT/USDC 提现样例，并跳转到财务审批。
4. 使用商户订单号查询支付状态。
5. 查询提现审批、签名、广播和确认事件时间线。

### 场景五：风险与验收

1. 打开 `Risk Control -> Strategy`，查看总暂停、单笔限额、日累计限额和地址策略。
2. 打开白名单和黑名单页面，验证受限地址处理。
3. 打开 `Management -> Demo Acceptance`，查看实时验收证据。
4. 打开审计日志，检查创建、审批、签名、回调和策略修改记录。

## 6. 自动测试

运行后端测试：

```bash
python3 -m unittest discover -s tests -v
```

如需构建网页托管版本，需要 Node.js 与 pnpm：

```bash
pnpm install
pnpm run build
node --test tests/rendered-html.test.mjs
```

## 7. 停止程序与重置数据

在运行程序的终端按 `Control + C` 停止。

本机业务数据默认保存在 `data/` 目录。数据库文件已被 Git 忽略，不会上传到代码仓库。

## 8. 安全说明

- 不要把助记词、私钥或真实签名密钥写入项目或聊天。
- 不要直接使用 Demo 默认密码部署到公网。
- 真实出款必须保留财务审批、出款暂停开关、限额、地址策略和独立签名边界。
- 接入真实链前，应先使用测试钱包和测试环境完成全链路验收。
