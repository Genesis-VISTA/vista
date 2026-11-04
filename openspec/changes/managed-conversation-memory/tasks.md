## 1. Backend

- [x] 1.1 Persist managed-memory settings and state on each conversation
- [x] 1.2 Build bounded context from summary, retrieved archive, and recent exact turns
- [x] 1.3 Compress old complete turns and update conversation-scoped retrieval after a run
- [x] 1.4 Keep the full transcript authoritative for successful and interrupted runs

## 2. UI

- [x] 2.1 Expose managed-memory and context-size controls in an open conversation
- [x] 2.2 Forward the same settings through the Next.js API routes

## 3. Checks

- [x] 3.1 Cover API persistence, context assembly, compression boundaries, and disabled memory
- [x] 3.2 Run the focused backend and UI checks
