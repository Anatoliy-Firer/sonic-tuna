const WIDTH = 64;
const HEIGHT = 64;

const SCAN_INTERVAL_MS = 1000;

function fillRgbFromRgba(rgbaBytes, rgbBytes) {
    let hasVisiblePixel = false;

    for (let src = 0, dst = 0; src < rgbaBytes.length; src += 4, dst += 3) {
        const red = rgbaBytes[src];
        const green = rgbaBytes[src + 1];
        const blue = rgbaBytes[src + 2];

        rgbBytes[dst] = red;
        rgbBytes[dst + 1] = green;
        rgbBytes[dst + 2] = blue;

        if (!hasVisiblePixel && (red !== 0 || green !== 0 || blue !== 0)) {
            hasVisiblePixel = true;
        }
    }

    return hasVisiblePixel;
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

function getFrameBridge() {
    const bridge = globalThis.__sonicTunaFrameBridge;
    if (!bridge || typeof bridge.sendFrame !== "function") {
        throw new Error("Shared frame bridge is unavailable");
    }

    return bridge;
}

async function streamRemoteVideo(entry, abortController) {
    const frameBridge = getFrameBridge();
    const processor = new MediaStreamTrackProcessor({track: entry.track});
    const reader = processor.readable.getReader();
    const canvas = new OffscreenCanvas(WIDTH, HEIGHT);
    const ctx = canvas.getContext("2d", {willReadFrequently: true});
    ctx.imageSmoothingEnabled = false;
    const rgbBytes = new Uint8Array(WIDTH * HEIGHT * 3);

    const stopReader = async () => {
        try {
            await reader.cancel();
        } catch (_) {
        }
    };

    entry.track.addEventListener("ended", () => abortController.abort(), {once: true});
    abortController.signal.addEventListener("abort", () => {
        stopReader();
    }, {once: true});

    while (!abortController.signal.aborted) {
        const {value: frame, done} = await reader.read();
        if (done) {
            break;
        }

        try {
            ctx.drawImage(frame, 0, 0, WIDTH, HEIGHT);
            const rgbaBytes = ctx.getImageData(0, 0, WIDTH, HEIGHT).data;
            if (!fillRgbFromRgba(rgbaBytes, rgbBytes)) {
                continue;
            }
            frameBridge.sendFrame(rgbBytes);
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
