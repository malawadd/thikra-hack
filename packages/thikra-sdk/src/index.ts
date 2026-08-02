export interface ThikraClientOptions { apiKey: string; baseUrl: string; fetch?: typeof globalThis.fetch }
export interface Money { amountMinor: number; currency: string }
export interface QuoteRequest {
  service: string;
  input: Record<string, unknown>;
  buyerPrincipal: { type?: 'HUMAN' | 'ORGANISATION' | 'UNKNOWN'; displayName: string; email?: string; organisation?: string; externalReference?: string };
  buyerAgent: { name: string; description?: string; developerName?: string; operatorName?: string; framework?: string; modelName?: string; modelVersion?: string; externalAgentId?: string; agentCardUrl?: string; metadata?: Record<string, unknown> };
  maximumBudget?: Money;
  callbackUrl?: string;
}
export interface Quote { id: string; service: string; status: string; currency: string; total_minor: number; expires_at: string; pricing_breakdown: Record<string, number>; mandate_preview: Record<string, unknown> }
export interface CommercialOrder { id: string; public_order_number: string; quote_id: string; status: string; currency: string; quoted_total_minor: number; paid_total_minor: number; payment_state: string; fulfillment_state: string; [key: string]: unknown }

export class ThikraApiError extends Error {
  constructor(readonly status: number, readonly code: string, message: string) { super(message); }
}

const idempotencyKey = () => globalThis.crypto.randomUUID();

export class ThikraClient {
  readonly services;
  readonly quotes;
  readonly orders;
  readonly payments;
  readonly deliverables;
  readonly disputes;
  private readonly requestFetch: typeof globalThis.fetch;
  private readonly baseUrl: string;
  private readonly apiKey: string;

  constructor(options: ThikraClientOptions) {
    this.apiKey = options.apiKey;
    this.baseUrl = options.baseUrl.replace(/\/$/, '');
    this.requestFetch = options.fetch ?? globalThis.fetch;
    this.services = {
      list: () => this.request<{ items: Record<string, unknown>[]; total: number }>('GET', '/api/v1/services'),
      get: (slug: string) => this.request<Record<string, unknown>>('GET', `/api/v1/services/${encodeURIComponent(slug)}`)
    };
    this.quotes = {
      create: (value: QuoteRequest) => this.request<Quote>('POST', '/api/v1/quotes', {
        service: value.service, input: value.input,
        buyer_principal: { type: value.buyerPrincipal.type ?? 'UNKNOWN', display_name: value.buyerPrincipal.displayName, email: value.buyerPrincipal.email, organisation: value.buyerPrincipal.organisation, external_reference: value.buyerPrincipal.externalReference },
        buyer_agent: { name: value.buyerAgent.name, description: value.buyerAgent.description ?? '', developer_name: value.buyerAgent.developerName, operator_name: value.buyerAgent.operatorName, framework: value.buyerAgent.framework, model_name: value.buyerAgent.modelName, model_version: value.buyerAgent.modelVersion, external_agent_id: value.buyerAgent.externalAgentId, agent_card_url: value.buyerAgent.agentCardUrl, metadata: value.buyerAgent.metadata ?? {} },
        maximum_budget: value.maximumBudget ? { amount_minor: value.maximumBudget.amountMinor, currency: value.maximumBudget.currency } : undefined,
        callback_url: value.callbackUrl
      }, true),
      get: (id: string) => this.request<Quote>('GET', `/api/v1/quotes/${id}`),
      accept: (id: string) => this.request<Quote>('POST', `/api/v1/quotes/${id}/accept`, {}, true)
    };
    this.orders = {
      create: (value: { quoteId: string; callbackUrl?: string; externalReference?: string }) => this.request<CommercialOrder>('POST', '/api/v1/orders', { quote_id: value.quoteId, callback_url: value.callbackUrl, external_reference: value.externalReference }, true),
      get: (id: string) => this.request<CommercialOrder>('GET', `/api/v1/orders/${id}`),
      status: (id: string) => this.request<CommercialOrder>('GET', `/api/v1/orders/${id}`),
      events: (id: string) => this.request<{items: Record<string, unknown>[]}>('GET', `/api/v1/orders/${id}/events`),
      start: (id: string) => this.request<CommercialOrder>('POST', `/api/v1/orders/${id}/start`, {}, true),
      startTestFulfillment: (id: string) => this.request<Record<string, any>>('POST', `/api/v1/orders/${id}/test-fulfillment`, {}, true),
      retry: (id: string, component='failed', reason='Verification failure') => this.request<CommercialOrder>('POST', `/api/v1/orders/${id}/retry`, { component, reason }, true),
      acceptDelivery: (id: string) => this.request<CommercialOrder>('POST', `/api/v1/orders/${id}/accept`, {}, true)
    };
    this.payments = {
      authorize: (orderId: string, userId: string, userEmail: string) => this.request<Record<string, any>>('POST', `/api/v1/orders/${orderId}/payment-authorization`, { user_id: userId, user_email: userEmail }, true),
      status: (orderId: string) => this.request<Record<string, any>>('GET', `/api/v1/orders/${orderId}/payment`),
      confirmDemo: (orderId: string, approvedBy: string) => this.request<Record<string, any>>('POST', `/api/v1/orders/${orderId}/payment/confirm-demo`, { approved_by: approvedBy, acknowledge_simulation: true }, true)
    };
    this.deliverables = {
      list: (orderId: string) => this.request<{deliverables: Record<string, any>[]}>('GET', `/api/v1/orders/${orderId}/deliverables`),
      receipt: (orderId: string) => this.request<Record<string, any>>('GET', `/api/v1/orders/${orderId}/delivery-receipt`),
      verifyReceipt: (receipt: Record<string, any>) => this.request<{valid:boolean}>('POST', '/api/v1/delivery-receipts/verify', { receipt_payload: receipt.receipt_payload, receipt_hash: receipt.receipt_hash, signature: receipt.signature, signing_key_id: receipt.signing_key_id })
    };
    this.disputes = {
      create: (orderId: string, reasonCode: string, description: string, deliverableId?: string) => this.request<Record<string, any>>('POST', `/api/v1/orders/${orderId}/disputes`, { reason_code: reasonCode, description, deliverable_id: deliverableId }, true),
      get: (id: string) => this.request<Record<string, any>>('GET', `/api/v1/disputes/${id}`)
    };
  }

  private async request<T>(method: string, path: string, body?: unknown, idempotent=false): Promise<T> {
    const response = await this.requestFetch(`${this.baseUrl}${path}`, {
      method,
      headers: { 'Authorization': `Bearer ${this.apiKey}`, 'Content-Type': 'application/json', ...(idempotent ? { 'Idempotency-Key': idempotencyKey() } : {}) },
      body: body === undefined ? undefined : JSON.stringify(body)
    });
    if (!response.ok) {
      const value = await response.json().catch(() => ({})) as any;
      const detail = value.detail ?? value;
      throw new ThikraApiError(response.status, detail.code ?? 'THIKRA_API_ERROR', detail.message ?? `Request failed (${response.status})`);
    }
    return response.json() as Promise<T>;
  }
}
