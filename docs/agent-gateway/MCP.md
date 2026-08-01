# MCP Agent Gateway

Thikra mounts authenticated MCP Streamable HTTP at `/mcp/` using the maintained Python MCP server. The parent FastAPI lifespan starts and stops the MCP session manager.

The 17 tools are: `thikra_list_services`, `thikra_get_service`, `thikra_request_quote`, `thikra_get_quote`, `thikra_accept_quote`, `thikra_create_order`, `thikra_get_order`, `thikra_create_payment_authorization`, `thikra_get_payment_status`, `thikra_start_order`, `thikra_get_order_status`, `thikra_get_order_events`, `thikra_get_deliverables`, `thikra_get_delivery_receipt`, `thikra_request_retry`, `thikra_open_dispute`, and `thikra_get_dispute`.

MCP imports only the shared Agent Gateway facade. Transport authentication uses the same scoped API keys as REST. Payment credentials, B2 credentials, private reasoning, and permanent asset URLs are never tool output. `tests/test_mcp.py` negotiates and calls the server through the official MCP client over Streamable HTTP.
