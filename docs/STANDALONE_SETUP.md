# Standalone Setup

Clinical Trial Matching Agent now carries its own setup contract and can be run without launching Leafy Hospital.

## Files To Create

- `backend/.env`
- `frontend/.env.local`

Start from:

- [backend/.env.example](../backend/.env.example)
- [frontend/.env.local.example](../frontend/.env.local.example)

## Local Run Commands

Backend:

```bash
cd backend
uv sync
uv run uvicorn main:app --reload --port 8014
```

Frontend:

```bash
cd frontend
npm install
npm run dev
```

## What The Standalone Shell Shows

- solution-manifest identity
- extracted backend inventory
- React compatibility inventory
- setup readiness for MongoDB, trial ingestion, linkage defaults, and optional reranking

## Compatibility Note

The `frontend/leafy-react-compat` directory is still preserved because Leafy continues to embed those React surfaces. The standalone Next.js shell is now independent from that compatibility bundle and boots with its own lightweight pages.
