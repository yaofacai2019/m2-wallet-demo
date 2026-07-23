const STORAGE_KEY = "m2-wallet-demo-v1";
const API_MODE = location.protocol === "http:" || location.protocol === "https:";
let currentUser = null;

const initialState = {
  payments: [
    { id: "PAY-260722-1024", merchant: "Nova Commerce", amount: 1250, orderCurrency: "USD", payCurrency: "USDT", network: "TRON", feeRate: .5, fee: 6.25, status: "paid", created: "10:42", address: "TUf7jW3xRkB8pM2aN9cQ5LzV6eD1HsY4", tx: "a83f...1d42" },
    { id: "PAY-260722-1023", merchant: "Orbit Market", amount: 680, orderCurrency: "USD", payCurrency: "USDC", network: "Polygon", feeRate: .5, fee: 3.4, status: "settled", created: "10:31", address: "0x71F4bE8F6A02c0918d2A61bF3Ee44AE9", tx: "0x62c1...e9a4" },
    { id: "PAY-260722-1022", merchant: "Atlas Digital", amount: 2340, orderCurrency: "USD", payCurrency: "USDT", network: "TRON", feeRate: .5, fee: 11.7, status: "pending", created: "10:18", address: "TVq9xA4eR7pL2mC8bN1kS5uD3fH6JzW0", tx: "" },
    { id: "PAY-260722-1021", merchant: "Nova Commerce", amount: 4200, orderCurrency: "USD", payCurrency: "USDT", network: "TRON", feeRate: .5, fee: 21, status: "settled", created: "09:56", address: "TEp3dB8yQ6cK1nF5vR9mX2aL7sW4HjU0", tx: "91bd...8c2e" },
    { id: "PAY-260722-1020", merchant: "TodayPay Global", amount: 890, orderCurrency: "EUR", payCurrency: "USDC", network: "Polygon", feeRate: .55, fee: 4.9, status: "paid", created: "09:41", address: "0x8a04E9b2F41c77D61a933b684Cd97218", tx: "0x7fc2...90b1" }
  ],
  withdrawals: [
    { id: "WD-260722-0881", merchant: "Nova Commerce", user: "user_89432", amount: 5200, currency: "USDT", network: "TRON", address: "TCu7mN4pQ8xR2kL6aB9vD3sF5jH1L9D2", status: "approval", created: "10:38", checks: "余额、地址正常", tx: "" },
    { id: "WD-260722-0880", merchant: "Orbit Market", user: "user_31047", amount: 1200, currency: "USDT", network: "TRON", address: "TFh2wK8rM4cP9vQ1sL6nB3aD7xE5YjU0", status: "approval", created: "10:26", checks: "余额、地址正常", tx: "" },
    { id: "WD-260722-0879", merchant: "Atlas Digital", user: "user_77602", amount: 3230, currency: "USDT", network: "TRON", address: "TLp6eS3nV8bR2xC9mK4aF1dQ7wH5ZjY0", status: "approval", created: "10:09", checks: "余额、地址正常", tx: "" },
    { id: "WD-260722-0878", merchant: "TodayPay Global", user: "user_12791", amount: 680, currency: "USDC", network: "Polygon", address: "0x903eA6d81b225a17A86fE73C6310D2A9", status: "confirmed", created: "09:44", checks: "已通过", tx: "0x74e1...9ac2" }
  ],
  completedBase: 16
};

let state = API_MODE ? { payments: [], withdrawals: [], callbacks: [], transactions: [], withdrawalEvents: [], reconciliation: {}, completedBase: 0 } : loadState();
let currentPaymentFilter = "all";

async function api(path, options = {}) {
  const response = await fetch(path, {
    ...options,
    credentials: "same-origin",
    headers: { "Content-Type": "application/json", ...(options.headers || {}) }
  });
  const payload = await response.json().catch(() => ({}));
  if (!response.ok) {
    const error = new Error(payload.error?.message || `请求失败 (${response.status})`);
    error.status = response.status;
    throw error;
  }
  return payload.data;
}

function roleCan(...roles) { return Boolean(currentUser && roles.includes(currentUser.role)); }
function showLogin(message = "") {
  document.getElementById("loginScreen").classList.remove("hidden");
  document.getElementById("loginError").textContent = message;
}
function setCurrentUser(user) {
  currentUser = user;
  const roleNames = { ADMIN: "系统管理员", FINANCE: "财务审批", OPERATOR: "运营人员", VIEWER: "只读访问" };
  document.getElementById("userInitials").textContent = user.username.slice(0, 2).toUpperCase();
  document.getElementById("userDisplayName").textContent = user.display_name;
  document.getElementById("userRole").textContent = roleNames[user.role] || user.role;
  document.getElementById("loginScreen").classList.add("hidden");
  applyPermissions();
}
function applyPermissions() {
  const canOperate = roleCan("ADMIN", "OPERATOR");
  const canApprove = roleCan("ADMIN", "FINANCE");
  document.getElementById("createPaymentButton").disabled = !canOperate;
  document.getElementById("createWithdrawalButton").disabled = !canOperate;
  document.getElementById("batchApproveButton").disabled = !canApprove;
}

function apiPayment(item) {
  return {
    id: item.id, merchant: item.merchant, amount: Number(item.amount), orderCurrency: item.order_currency,
    payCurrency: item.pay_currency, network: item.network === "POLYGON" ? "Polygon" : item.network,
    feeRate: Number(item.fee_rate_bps) / 100, fee: Number(item.fee_amount),
    status: item.status === "CONFIRMED" ? "paid" : "pending",
    created: new Date(item.created_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
    address: item.pay_address, tx: item.tx_hash || ""
  };
}

function apiWithdrawal(item) {
  const statuses = { PENDING_APPROVAL: "approval", APPROVED: "sending", SIGNING: "sending", BROADCASTED: "sending", CONFIRMED: "confirmed", REJECTED: "rejected", FAILED: "rejected" };
  return {
    id: item.id, merchant: item.merchant, user: item.user_id, amount: Number(item.amount), currency: item.currency,
    network: item.network === "POLYGON" ? "Polygon" : item.network, address: item.to_address,
    status: statuses[item.status] || item.status.toLowerCase(),
    created: new Date(item.created_at).toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }),
    checks: item.status === "CONFIRMED" ? "已通过" : item.status === "REJECTED" ? "已拒绝" : "余额、地址正常",
    tx: item.tx_hash || ""
  };
}

async function refreshApiState() {
  if (!API_MODE) return;
  try {
    const [payments, withdrawals, callbacks, transactions, withdrawalEvents, reconciliation] = await Promise.all([
      api("/api/v1/payment-orders"), api("/api/v1/withdrawals"), api("/api/v1/callbacks"),
      api("/api/v1/transactions"), api("/api/v1/withdrawal-events"), api("/api/v1/reconciliation")
    ]);
    state.payments = payments.map(apiPayment);
    state.withdrawals = withdrawals.map(apiWithdrawal);
    state.callbacks = callbacks; state.transactions = transactions; state.withdrawalEvents = withdrawalEvents; state.reconciliation = reconciliation;
    render();
  } catch (error) {
    toast(`后端连接失败：${error.message}`);
  }
}

function loadState() {
  try { return JSON.parse(localStorage.getItem(STORAGE_KEY)) || structuredClone(initialState); }
  catch { return structuredClone(initialState); }
}
function saveState() { localStorage.setItem(STORAGE_KEY, JSON.stringify(state)); }
function money(value) { return Number(value).toLocaleString("zh-CN", { minimumFractionDigits: 2, maximumFractionDigits: 2 }); }
function statusLabel(status) { return ({ pending: "待支付", paid: "已到账", settled: "已结算", approval: "待审批", sending: "发送中", confirmed: "已完成", rejected: "已拒绝" })[status] || status; }
function truncate(value, head = 7, tail = 5) { return `${value.slice(0, head)}...${value.slice(-tail)}`; }

const titleMap = {
  dashboard: ["资金总览", "稳定币收款、佣金与提现状态一目了然"],
  payments: ["稳定币收款", "为客户生成收款订单，自动确认 USDT / USDC 到账并计算佣金"],
  withdrawals: ["提 U 审批", "财务确认后，系统自动向用户地址发送稳定币"],
  operations: ["运营中心", "全局交易、异常回调、状态轨迹和账本对账"],
  wallets: ["钱包与账户", "查看出款余额、收款归集和资金策略"],
  integration: ["开发接入", "客户平台通过 API 接入稳定币收付服务"]
};

function switchView(view) {
  document.querySelectorAll(".view").forEach(el => el.classList.toggle("active", el.id === `${view}View`));
  document.querySelectorAll(".nav-item").forEach(el => el.classList.toggle("active", el.dataset.view === view));
  document.getElementById("pageTitle").textContent = titleMap[view][0];
  document.getElementById("pageSubtitle").textContent = titleMap[view][1];
  window.scrollTo({ top: 0, behavior: "smooth" });
}

function render() { renderPayments(); renderWithdrawals(); renderDashboard(); renderMetrics(); renderOperations(); }

function escapeHtml(value) { return String(value ?? "").replace(/[&<>'"]/g, char => ({"&":"&amp;","<":"&lt;",">":"&gt;","'":"&#39;",'"':"&quot;"})[char]); }
function shortHash(value) { return value ? truncate(value, 8, 6) : "—"; }
function displayTime(value) { return value ? new Date(value).toLocaleString("zh-CN", { month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit" }) : "—"; }

function renderOperations() {
  if (!document.getElementById("operationsView")) return;
  const reconciliation = state.reconciliation || {};
  const callbacks = state.callbacks || [], transactions = state.transactions || [], events = state.withdrawalEvents || [];
  const failedCallbacks = Number(reconciliation.failed_callbacks || callbacks.filter(item => item.status === "FAILED").length);
  const imbalanced = reconciliation.imbalanced_journals || [];
  document.getElementById("opsPendingPayments").textContent = reconciliation.pending_payments || 0;
  document.getElementById("opsOpenWithdrawals").textContent = reconciliation.open_withdrawals || 0;
  document.getElementById("opsFailedCallbacks").textContent = failedCallbacks;
  document.getElementById("opsImbalanced").textContent = imbalanced.length;
  document.getElementById("operationBadge").textContent = failedCallbacks;
  const reconcileStatus = document.getElementById("reconcileStatus");
  reconcileStatus.textContent = reconciliation.ok === false ? "发现异常" : "账本平衡";
  reconcileStatus.classList.toggle("failed", reconciliation.ok === false);
  document.getElementById("transactionCount").textContent = `${transactions.length} 笔`;
  document.getElementById("transactionRows").innerHTML = transactions.slice(0, 50).map(item => `<tr><td><strong>${escapeHtml(item.reference_id)}</strong><small>${escapeHtml(item.type)}</small></td><td>${escapeHtml(item.merchant)}</td><td><span class="direction ${item.direction.toLowerCase()}">${item.direction === "IN" ? "转入" : "转出"}</span></td><td><strong>${escapeHtml(item.asset)}</strong><small>${escapeHtml(item.network)}</small></td><td><strong>${money(item.amount)} ${escapeHtml(item.asset)}</strong></td><td><span class="status ${String(item.status).toLowerCase()}">${escapeHtml(item.status)}</span></td><td><code>${escapeHtml(shortHash(item.tx_hash))}</code></td><td>${displayTime(item.updated_at)}</td></tr>`).join("") || `<tr><td colspan="8">暂无交易</td></tr>`;
  document.getElementById("callbackCount").textContent = `${callbacks.length} 条`;
  document.getElementById("callbackRows").innerHTML = callbacks.slice(0, 30).map(item => { let target = "未配置"; try { if (item.callback_url) target = new URL(item.callback_url).hostname; } catch {} return `<tr><td><strong>${escapeHtml(item.event_type)}</strong><small>${escapeHtml(item.reference_id)}</small></td><td><span class="callback-status ${String(item.status).toLowerCase()}">${escapeHtml(item.status)}</span></td><td>${item.attempts}</td><td><span class="callback-target">${escapeHtml(target)}</span></td><td>${["FAILED","RETRY"].includes(item.status) ? `<button class="row-action primary" data-retry-callback="${escapeHtml(item.id)}">重试</button>` : ""}</td></tr>`; }).join("") || `<tr><td colspan="5">暂无回调事件</td></tr>`;
  document.getElementById("eventCount").textContent = `${events.length} 条`;
  document.getElementById("withdrawalEventRows").innerHTML = events.slice(0, 30).map(item => `<div class="timeline-item"><i></i><div><strong>${escapeHtml(item.status)}</strong><span>${escapeHtml(item.withdrawal_id)}</span><small>${escapeHtml(item.detail || "状态已更新")} · ${displayTime(item.created_at)}</small></div></div>`).join("") || `<div class="empty-ops">暂无提现状态事件</div>`;
}

function renderMetrics() {
  const pending = state.withdrawals.filter(w => w.status === "approval");
  const sending = state.withdrawals.filter(w => w.status === "sending");
  const completed = state.withdrawals.filter(w => w.status === "confirmed").length;
  document.getElementById("pendingMetric").innerHTML = `${pending.length} <small>笔</small>`;
  document.getElementById("pendingAmount").textContent = money(pending.reduce((sum, w) => sum + w.amount, 0));
  document.getElementById("withdrawBadge").textContent = pending.length;
  document.getElementById("approvalCount").textContent = `${pending.length} 笔`;
  document.getElementById("queuePendingCount").textContent = pending.length;
  document.getElementById("queueSendingCount").textContent = sending.length;
  document.getElementById("queueCompleteCount").textContent = state.completedBase + completed;
  document.getElementById("paymentBadge").textContent = state.payments.filter(p => p.status === "pending").length;
}

function renderDashboard() {
  const recent = [
    ...state.payments.slice(0, 3).map(p => ({ type: "in", title: `${p.merchant} 收款`, sub: `${p.payCurrency} · ${p.network}`, amount: `+${money(p.amount)}`, status: p.status })),
    ...state.withdrawals.slice(0, 2).map(w => ({ type: "out", title: `${w.merchant} 提现`, sub: `${w.currency} · ${w.network}`, amount: `-${money(w.amount)}`, status: w.status }))
  ].slice(0, 5);
  document.getElementById("dashboardFlow").innerHTML = recent.map(item => `<div class="flow-item"><span class="flow-icon">${item.type === "in" ? "↙" : "↗"}</span><p><strong>${item.title}</strong><small>${item.sub}</small></p><span class="status ${item.status}">${statusLabel(item.status)}</span><span class="flow-amount">${item.amount}</span></div>`).join("");
  const pending = state.withdrawals.filter(w => w.status === "approval");
  document.getElementById("approvalPreview").innerHTML = pending.length ? pending.slice(0, 3).map(w => `<div class="approval-item"><p><strong>${w.merchant}</strong><small>${truncate(w.address)} · ${w.created}</small></p><span class="money">${money(w.amount)} U</span></div>`).join("") : `<div class="detail-status"><div class="big-icon">✓</div><h2>全部处理完成</h2><p>目前没有待确认提现</p></div>`;
}

function renderPayments() {
  const rows = state.payments.filter(p => currentPaymentFilter === "all" || p.status === currentPaymentFilter);
  document.getElementById("paymentRows").innerHTML = rows.map(p => `<tr><td><strong>${p.id}</strong><small>${p.merchant}</small></td><td>${p.merchant}</td><td><strong>${money(p.amount)} ${p.orderCurrency}</strong><small>${p.payCurrency} 支付</small></td><td>${p.payCurrency} · ${p.network}</td><td>${money(p.fee)} ${p.orderCurrency}</td><td><span class="status ${p.status}">${statusLabel(p.status)}</span></td><td>${p.created}</td><td><button class="row-action ${p.status === "pending" ? "primary" : ""}" data-payment-id="${p.id}">${p.status === "pending" ? "模拟到账" : "查看"}</button></td></tr>`).join("") || `<tr><td colspan="8">暂无订单</td></tr>`;
  document.getElementById("paymentTotalCount").textContent = 19 + state.payments.length;
  const confirmed = state.payments.filter(p => ["paid", "settled"].includes(p.status));
  document.getElementById("paymentTotalAmount").textContent = `${money(14560 + confirmed.reduce((s, p) => s + p.amount, 0))} USD`;
  document.getElementById("paymentTotalFees").textContent = `${money(72.8 + confirmed.reduce((s, p) => s + p.fee, 0))} USD`;
}

function renderWithdrawals() {
  document.getElementById("withdrawRows").innerHTML = state.withdrawals.map(w => `<tr><td><strong>${w.id}</strong><small>${w.network}</small></td><td><strong>${w.merchant}</strong><small>${w.user}</small></td><td><strong>${money(w.amount)} ${w.currency}</strong></td><td><strong>${truncate(w.address)}</strong><small>${w.network}</small></td><td><span class="status confirmed">${w.checks}</span></td><td><span class="status ${w.status}">${statusLabel(w.status)}</span></td><td>${w.created}</td><td>${w.status === "approval" ? `<button class="row-action primary" data-withdraw-id="${w.id}">确认发送</button>` : `<button class="row-action" data-withdraw-id="${w.id}">查看</button>`}</td></tr>`).join("");
}

function showPaymentDetail(id) {
  const p = state.payments.find(item => item.id === id); if (!p) return;
  const isPending = p.status === "pending";
  const canSimulate = isPending && roleCan("ADMIN", "OPERATOR");
  document.getElementById("paymentDetail").innerHTML = `<div class="dialog-head"><div><span class="kicker">PAYMENT ORDER</span><h2>${p.id}</h2></div><button class="dialog-close" data-close-dialog="paymentDetailDialog">×</button></div><div class="detail-status"><div class="big-icon">${isPending ? "⌛" : "✓"}</div><h2>${isPending ? "等待用户支付" : "资金已确认到账"}</h2><p>${p.merchant} · ${money(p.amount)} ${p.orderCurrency}</p></div><div class="pay-address"><span>用户支付地址</span><div><code>${p.address}</code><button class="copy-button" data-copy="${p.address}">复制地址</button></div></div><div class="detail-grid"><div><span>支付币种</span><strong>${p.payCurrency}</strong></div><div><span>网络</span><strong>${p.network}</strong></div><div><span>平台佣金</span><strong>${money(p.fee)} ${p.orderCurrency}</strong></div><div><span>链上交易</span><strong>${p.tx || "等待检测"}</strong></div></div>${canSimulate ? `<div class="dialog-actions"><button class="secondary-button" data-close-dialog="paymentDetailDialog">关闭</button><button class="primary-button" data-simulate-payment="${p.id}">模拟用户已支付</button></div>` : `<div class="dialog-actions"><button class="primary-button" data-close-dialog="paymentDetailDialog">完成</button></div>`}`;
  document.getElementById("paymentDetailDialog").showModal();
}

function showWithdrawDetail(id) {
  const w = state.withdrawals.find(item => item.id === id); if (!w) return;
  const canApprove = w.status === "approval" && roleCan("ADMIN", "FINANCE");
  document.getElementById("withdrawDetail").innerHTML = `<div class="dialog-head"><div><span class="kicker">WITHDRAWAL REVIEW</span><h2>${w.id}</h2></div><button class="dialog-close" data-close-dialog="withdrawDialog">×</button></div><div class="detail-status"><div class="big-icon">${canApprove ? "↗" : "✓"}</div><h2>${canApprove ? `${money(w.amount)} ${w.currency}` : statusLabel(w.status)}</h2><p>${w.merchant} · ${w.user}</p></div><div class="pay-address"><span>用户收款地址</span><div><code>${w.address}</code><button class="copy-button" data-copy="${w.address}">复制地址</button></div></div><div class="detail-grid"><div><span>网络</span><strong>${w.network}</strong></div><div><span>币种</span><strong>${w.currency}</strong></div><div><span>检查结果</span><strong>${w.checks}</strong></div><div><span>交易哈希</span><strong>${w.tx || "尚未广播"}</strong></div></div>${canApprove ? `<div class="approve-warning">确认后，系统将锁定 ${money(w.amount)} ${w.currency}，调用签名服务并广播交易。Demo 环境只模拟这一过程，不发送真实资金。</div><div class="dialog-actions"><button class="secondary-button" data-reject-withdraw="${w.id}">拒绝</button><button class="primary-button" data-approve-withdraw="${w.id}">财务确认并自动发送</button></div>` : `<div class="dialog-actions"><button class="primary-button" data-close-dialog="withdrawDialog">完成</button></div>`}`;
  document.getElementById("withdrawDialog").showModal();
}

async function simulatePayment(id) {
  const p = state.payments.find(item => item.id === id); if (!p || p.status !== "pending") return;
  if (API_MODE) {
    try {
      await api(`/api/v1/payment-orders/${id}/simulate-confirm`, { method: "POST", body: "{}" });
      document.getElementById("paymentDetailDialog").close();
      await refreshApiState();
      toast(`已检测 ${money(p.amount)} ${p.payCurrency} 到账，佣金已写入复式账本`);
    } catch (error) { toast(`确认失败：${error.message}`); }
    return;
  }
  p.status = "paid"; p.tx = `${p.network === "TRON" ? "b19e" : "0x84d2"}...${Math.random().toString(16).slice(2, 6)}`; saveState(); render();
  document.getElementById("paymentDetailDialog").close(); toast(`已检测 ${money(p.amount)} ${p.payCurrency} 到账，佣金 ${money(p.fee)} ${p.orderCurrency} 已入账`);
}

async function approveWithdraw(id) {
  const w = state.withdrawals.find(item => item.id === id); if (!w || w.status !== "approval") return;
  if (API_MODE) {
    try {
      await api(`/api/v1/withdrawals/${id}/approve`, { method: "POST", body: "{}" });
      document.getElementById("withdrawDialog").close();
      await refreshApiState();
      toast(`${money(w.amount)} ${w.currency} 已完成模拟签名与发送，交易哈希已回写`);
    } catch (error) { toast(`审批失败：${error.message}`); }
    return;
  }
  w.status = "sending"; saveState(); render(); document.getElementById("withdrawDialog").close(); toast("财务已确认，正在锁定资金并请求签名…");
  setTimeout(() => { w.status = "confirmed"; w.checks = "已通过"; w.tx = `${w.network === "TRON" ? "8f21" : "0x912a"}...${Math.random().toString(16).slice(2, 6)}`; saveState(); render(); toast(`${money(w.amount)} ${w.currency} 已模拟发送，交易哈希已回写`); }, 1800);
}

async function rejectWithdraw(id) {
  const w = state.withdrawals.find(item => item.id === id); if (!w) return;
  if (API_MODE) {
    try {
      await api(`/api/v1/withdrawals/${id}/reject`, { method: "POST", body: "{}" });
      document.getElementById("withdrawDialog").close();
      await refreshApiState(); toast("提现已拒绝，未创建链上交易");
    } catch (error) { toast(`拒绝失败：${error.message}`); }
    return;
  }
  w.status = "rejected"; saveState(); render(); document.getElementById("withdrawDialog").close(); toast("提现已拒绝，未创建链上交易");
}

function toast(message) { const el = document.getElementById("toast"); el.textContent = message; el.classList.add("show"); clearTimeout(window.toastTimer); window.toastTimer = setTimeout(() => el.classList.remove("show"), 3200); }
function copyText(value) { navigator.clipboard?.writeText(value).then(() => toast("已复制到剪贴板")).catch(() => toast(value)); }

document.addEventListener("click", event => {
  const target = event.target.closest("button"); if (!target) return;
  if (target.dataset.view) switchView(target.dataset.view);
  if (target.dataset.viewJump) switchView(target.dataset.viewJump);
  if (target.dataset.closeDialog) document.getElementById(target.dataset.closeDialog).close();
  if (target.dataset.copy) copyText(target.dataset.copy);
  if (target.dataset.paymentId) showPaymentDetail(target.dataset.paymentId);
  if (target.dataset.withdrawId) showWithdrawDetail(target.dataset.withdrawId);
  if (target.dataset.simulatePayment) simulatePayment(target.dataset.simulatePayment);
  if (target.dataset.approveWithdraw) approveWithdraw(target.dataset.approveWithdraw);
  if (target.dataset.rejectWithdraw) rejectWithdraw(target.dataset.rejectWithdraw);
  if (target.dataset.retryCallback) retryCallback(target.dataset.retryCallback);
  if (target.dataset.paymentFilter) { currentPaymentFilter = target.dataset.paymentFilter; document.querySelectorAll("[data-payment-filter]").forEach(el => el.classList.toggle("active", el === target)); renderPayments(); }
});

async function retryCallback(id) {
  try { await api(`/api/v1/callbacks/${id}/retry`, { method: "POST", body: "{}" }); await refreshApiState(); toast("回调已重新加入发送队列"); }
  catch (error) { toast(`重试失败：${error.message}`); }
}

document.getElementById("createPaymentButton").addEventListener("click", () => {
  document.getElementById("merchantOrderId").value = `M${Date.now().toString().slice(-10)}`;
  document.getElementById("paymentDialog").showModal();
});
document.getElementById("createWithdrawalButton").addEventListener("click", () => {
  document.getElementById("merchantWithdrawId").value = `W${Date.now().toString().slice(-10)}`;
  document.getElementById("createWithdrawalDialog").showModal();
});
document.getElementById("withdrawNetwork").addEventListener("change", event => {
  const polygon = event.currentTarget.value === "POLYGON";
  document.getElementById("withdrawCurrency").value = polygon ? "USDC" : "USDT";
  document.getElementById("withdrawAddress").value = polygon ? `0x${"1".repeat(40)}` : `T${"1".repeat(33)}`;
});
document.getElementById("withdrawCurrency").addEventListener("change", event => {
  const polygon = event.currentTarget.value === "USDC";
  document.getElementById("withdrawNetwork").value = polygon ? "POLYGON" : "TRON";
  document.getElementById("withdrawAddress").value = polygon ? `0x${"1".repeat(40)}` : `T${"1".repeat(33)}`;
});
document.getElementById("withdrawalForm").addEventListener("submit", async event => {
  event.preventDefault(); const data = new FormData(event.currentTarget);
  if (!API_MODE) { toast("请通过本地服务打开页面以创建真实 Demo 提现单"); return; }
  try {
    await api("/api/v1/withdrawals", {
      method: "POST",
      body: JSON.stringify({ merchant_withdraw_id: data.get("merchantWithdrawId"), merchant: data.get("merchant"), user_id: data.get("userId"), amount: String(data.get("amount")), currency: data.get("currency"), network: data.get("network"), to_address: data.get("toAddress") })
    });
    await refreshApiState(); document.getElementById("createWithdrawalDialog").close(); switchView("withdrawals");
    toast("提现申请已写入数据库，等待财务审批");
  } catch (error) { toast(`创建失败：${error.message}`); }
});
document.getElementById("paymentForm").addEventListener("input", event => {
  const form = event.currentTarget; document.getElementById("feePreview").textContent = `${money(Number(form.amount.value || 0) * Number(form.feeRate.value || 0) / 100)} ${form.orderCurrency.value}`;
});
document.getElementById("paymentForm").addEventListener("submit", async event => {
  event.preventDefault(); const data = new FormData(event.currentTarget); const amount = Number(data.get("amount")); const feeRate = Number(data.get("feeRate")); const network = data.get("network");
  if (API_MODE) {
    try {
      await api("/api/v1/payment-orders", {
        method: "POST",
        body: JSON.stringify({
          merchant_order_id: data.get("merchantOrderId"), merchant: data.get("merchant"), amount: String(data.get("amount")),
          order_currency: data.get("orderCurrency"), pay_currency: data.get("payCurrency"), network: String(network).toUpperCase()
        })
      });
      await refreshApiState(); document.getElementById("paymentDialog").close(); switchView("payments");
      toast("收款订单已写入数据库，已生成唯一支付地址");
    } catch (error) { toast(`创建失败：${error.message}`); }
    return;
  }
  const id = `PAY-${new Date().toISOString().slice(2,10).replaceAll("-","")}-${String(1025 + state.payments.length).padStart(4,"0")}`;
  state.payments.unshift({ id, merchant: data.get("merchant"), amount, orderCurrency: data.get("orderCurrency"), payCurrency: data.get("payCurrency"), network, feeRate, fee: amount * feeRate / 100, status: "pending", created: new Date().toLocaleTimeString("zh-CN", { hour: "2-digit", minute: "2-digit" }), address: network === "TRON" ? `T${Math.random().toString(36).slice(2, 34).padEnd(33,"X")}` : `0x${Array.from({length:40},()=>Math.floor(Math.random()*16).toString(16)).join("")}`, tx: "" });
  saveState(); render(); document.getElementById("paymentDialog").close(); switchView("payments"); toast("收款订单已创建，已生成唯一支付地址");
});
document.getElementById("batchApproveButton").addEventListener("click", () => { const small = state.withdrawals.filter(w => w.status === "approval" && w.amount <= 1500); if (!small.length) return toast("没有可批量处理的小额订单"); small.forEach(w => approveWithdraw(w.id)); });
document.getElementById("resetButton").addEventListener("click", () => {
  if (API_MODE) { refreshApiState(); toast("已从数据库刷新数据"); return; }
  if (confirm("确定重置所有 Demo 数据吗？")) { state = structuredClone(initialState); saveState(); render(); switchView("dashboard"); toast("Demo 数据已重置"); }
});
setInterval(() => { document.getElementById("clock").textContent = new Date().toLocaleString("zh-CN", { month:"2-digit", day:"2-digit", hour:"2-digit", minute:"2-digit", second:"2-digit" }); }, 1000);
render();
refreshApiState();
