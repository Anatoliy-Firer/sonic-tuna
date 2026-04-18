([WIDTH, HEIGHT]) => {

    const SCAN_INTERVAL_MS = 1000;
    const SIGNATURE_DISTANCE_THRESHOLD = 50;
    const SIGNATURE_LT = 255;
    const SIGNATURE_RT = 0;
    const SIGNATURE_LB = 0;
    const SIGNATURE_RB = 255;

    function fillGrayFromRgba(rgbaBytes, grayBytes) {
        const greenAt = (x, y) => {
            return rgbaBytes[(y * WIDTH + x) * 4 + 1];
        };

        const lt = greenAt(0, 0);
        const rt = greenAt(0, HEIGHT - 1);
        const lb = greenAt(WIDTH - 1, 0);
        const rb = greenAt(WIDTH - 1, HEIGHT - 1);

        if (!(Math.abs(lt - SIGNATURE_LT) <= SIGNATURE_DISTANCE_THRESHOLD
            && Math.abs(rt - SIGNATURE_RT) <= SIGNATURE_DISTANCE_THRESHOLD
            && Math.abs(lb - SIGNATURE_LB) <= SIGNATURE_DISTANCE_THRESHOLD
            && Math.abs(rb - SIGNATURE_RB) <= SIGNATURE_DISTANCE_THRESHOLD)) {
            return false;
        }

        for (let i = 0; i < grayBytes.length; i += 1) {
            grayBytes[i] = rgbaBytes[i * 4 + 1];
        }
        return true;
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
        const grayBytes = new Uint8Array(WIDTH * HEIGHT);

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
                if (!fillGrayFromRgba(rgbaBytes, grayBytes)) {
                    continue;
                }
                frameBridge.sendFrame(grayBytes);
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
}
