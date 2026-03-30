const WIDTH = 256;
const HEIGHT = 256;
const WS_URL = "ws://localhost:8000/incoming-frames";
const SCAN_INTERVAL_MS = 1000;

function hasVisiblePixel(rgbaBytes) {
    for (let index = 0; index < rgbaBytes.length; index += 4) {
        if (rgbaBytes[index] !== 0 || rgbaBytes[index + 1] !== 0 || rgbaBytes[index + 2] !== 0) {
            return true;
        }
    }

    return false;
}

function rgbFromRgba(rgbaBytes) {
    const rgbBytes = new Uint8Array(WIDTH * HEIGHT * 3);
    for (let src = 0, dst = 0; src < rgbaBytes.length; src += 4, dst += 3) {
        rgbBytes[dst] = rgbaBytes[src];
        rgbBytes[dst + 1] = rgbaBytes[src + 1];
        rgbBytes[dst + 2] = rgbaBytes[src + 2];
    }

    return rgbBytes;
}

function getRemoteVideoEntries() {
    const videos = Array.from(document.getElementsByTagName("video"));
    const entries = [];

    videos.forEach((video, index) => {
        if (index === 0) {
            return;
        }

        const stream = video.srcObject;
        if (!(stream instanceof MediaStream) || !stream.active) {
            return;
        }

        const [track] = stream.getVideoTracks();
        if (!track || track.readyState !== "live") {
            return;
        }

        entries.push({
            sourceId: `${stream.id}:${track.id}`,
            stream,
            track,
            video
        });
    });

    return entries;
}

async function openSocket(sourceId, abortSignal) {
    return await new Promise((resolve, reject) => {
        if (abortSignal.aborted) {
            reject(new DOMException("Capture aborted", "AbortError"));
            return;
        }

        const socket = new WebSocket(`${WS_URL}?source_id=${encodeURIComponent(sourceId)}`);
        socket.binaryType = "arraybuffer";

        const abortHandler = () => {
            socket.close();
            reject(new DOMException("Capture aborted", "AbortError"));
        };

        abortSignal.addEventListener("abort", abortHandler, {once: true});
        socket.onopen = () => {
            abortSignal.removeEventListener("abort", abortHandler);
            resolve(socket);
        };
        socket.onerror = () => {
            abortSignal.removeEventListener("abort", abortHandler);
            reject(new Error(`WebSocket connection failed for ${sourceId}`));
        };
    });
}

async function streamRemoteVideo(entry, abortController) {
    const socket = await openSocket(entry.sourceId, abortController.signal);
    const processor = new MediaStreamTrackProcessor({track: entry.track});
    const reader = processor.readable.getReader();
    const canvas = new OffscreenCanvas(WIDTH, HEIGHT);
    const ctx = canvas.getContext("2d", {willReadFrequently: true});

    const stopReader = async () => {
        try {
            await reader.cancel();
        } catch (_) {
        }
    };

    entry.track.addEventListener("ended", () => abortController.abort(), {once: true});
    abortController.signal.addEventListener("abort", () => {
        stopReader();
        if (socket.readyState === WebSocket.OPEN || socket.readyState === WebSocket.CONNECTING) {
            socket.close();
        }
    }, {once: true});

    while (!abortController.signal.aborted) {
        const {value: frame, done} = await reader.read();
        if (done) {
            break;
        }

        try {
            ctx.drawImage(frame, 0, 0, WIDTH, HEIGHT);
            const rgbaBytes = ctx.getImageData(0, 0, WIDTH, HEIGHT).data;
            if (!hasVisiblePixel(rgbaBytes)) {
                continue;
            }

            if (socket.readyState === WebSocket.OPEN) {
                socket.send(rgbFromRgba(rgbaBytes));
            }
        } finally {
            frame.close();
        }
    }
}

const activeCaptures = new Map();
let scanScheduled = false;

function stopCapture(sourceId) {
    const capture = activeCaptures.get(sourceId);
    if (!capture) {
        return;
    }

    capture.abortController.abort();
    activeCaptures.delete(sourceId);
}

function scheduleScan() {
    if (scanScheduled) {
        return;
    }

    scanScheduled = true;
    queueMicrotask(() => {
        scanScheduled = false;
        syncCaptures();
    });
}

function syncCaptures() {
    const remoteEntries = getRemoteVideoEntries();
    const nextIds = new Set(remoteEntries.map((entry) => entry.sourceId));

    for (const sourceId of activeCaptures.keys()) {
        if (!nextIds.has(sourceId)) {
            stopCapture(sourceId);
        }
    }

    for (const entry of remoteEntries) {
        if (activeCaptures.has(entry.sourceId)) {
            continue;
        }

        const abortController = new AbortController();
        const runPromise = streamRemoteVideo(entry, abortController).catch((error) => {
            if (error?.name !== "AbortError") {
                console.error(error);
            }
        }).finally(() => {
            const current = activeCaptures.get(entry.sourceId);
            if (current?.abortController === abortController) {
                activeCaptures.delete(entry.sourceId);
                scheduleScan();
            }
        });

        activeCaptures.set(entry.sourceId, {
            abortController,
            runPromise
        });
    }
}

const observer = new MutationObserver(() => {
    scheduleScan();
});

if (document.body) {
    observer.observe(document.body, {
        childList: true,
        subtree: true,
        attributes: true,
        attributeFilter: ["srcObject", "class", "style"]
    });
}

setInterval(syncCaptures, SCAN_INTERVAL_MS);
syncCaptures();
