import uuid
import asyncio
from concurrent.futures import ProcessPoolExecutor
from contextlib import asynccontextmanager
from pathlib import Path
from dataclasses import dataclass, field
from enum import Enum
from fastapi import FastAPI, UploadFile, File, HTTPException

from pipeline import run_pipeline, _init_worker


class JobStatus(str, Enum):
    pending = "pending"
    running = "running"
    done = "done"
    failed = "failed"


@dataclass
class Job:
    id: str
    status: JobStatus = JobStatus.pending
    result: dict | None = None
    error: str | None = None


jobs: dict[str, Job] = {}


@asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.executor = ProcessPoolExecutor(
        max_workers=4,
        initializer=_init_worker,
    )
    yield
    app.state.executor.shutdown()


app = FastAPI(lifespan=lifespan)
UPLOAD_DIR = Path("/tmp/uploads")


@app.post("/extract")
async def extract(file: UploadFile = File(...)):
    UPLOAD_DIR.mkdir(parents=True, exist_ok=True)
    file_path = UPLOAD_DIR / file.filename
    file_path.write_bytes(await file.read())
    output_dir = UPLOAD_DIR / "output"

    job_id = str(uuid.uuid4())
    jobs[job_id] = Job(id=job_id)

    loop = asyncio.get_running_loop()

    async def _run():
        jobs[job_id].status = JobStatus.running
        try:
            jobs[job_id].result = await loop.run_in_executor(
                app.state.executor,
                run_pipeline,
                str(file_path),
                str(output_dir),
            )
            jobs[job_id].status = JobStatus.done
        except Exception as e:
            jobs[job_id].error = str(e)
            jobs[job_id].status = JobStatus.failed

    asyncio.create_task(_run())  # fire-and-forget
    return {"job_id": job_id}


@app.get("/jobs/{job_id}")
async def get_job(job_id: str):
    job = jobs.get(job_id)
    if not job:
        raise HTTPException(404, "Job not found")
    return {
        "job_id": job.id,
        "status": job.status,
        "result": job.result,
        "error": job.error,
    }
