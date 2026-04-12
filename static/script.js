document.addEventListener('DOMContentLoaded', () => {
    // ── DOM Elements ─────────────────────────────────────────────────────
    const guidelineInput    = document.getElementById('guideline-input');
    const guidelineStatus   = document.getElementById('guideline-status');
    const drawingInput      = document.getElementById('drawing-input');
    const analyzeBtn        = document.getElementById('analyze-btn');
    const loadingOverlay    = document.getElementById('loading-overlay');
    const guidelineList     = document.getElementById('guideline-list');
    const drawingList       = document.getElementById('drawing-list');
    const drawingStatus     = document.getElementById('drawing-status');

    // Viewer
    const drawingImage      = document.getElementById('drawing-image');
    const markupLayer       = document.getElementById('markup-layer');
    const errorCountBadge   = document.getElementById('error-count-badge');
    const errorsTableBody   = document.querySelector('#errors-table tbody');

    // Page navigation
    const pageNav           = document.getElementById('page-nav');
    const prevPageBtn       = document.getElementById('prev-page-btn');
    const nextPageBtn       = document.getElementById('next-page-btn');
    const pageIndicator     = document.getElementById('page-indicator');

    // ── State ────────────────────────────────────────────────────────────
    let currentErrors        = [];
    let currentDrawingFilename = '';
    let markedUpPdfUrl       = '';

    // Multi-page state
    let allPageUrls          = [];   // ["/drawings/stem_page_0.png", ...]
    let pageDims             = [];   // [{width, height}] in PDF points
    let currentPageIdx       = 0;

    // ── File Lists ───────────────────────────────────────────────────────
    async function fetchFiles(type) {
        try {
            const endpoint = type === 'guidelines' ? '/api/guidelines' : '/api/drawings';
            const res  = await fetch(endpoint);
            const data = await res.json();
            renderFileList(type, data.files);
        } catch (err) {
            console.error(`Failed to fetch ${type}:`, err);
        }
    }

    function renderFileList(type, files) {
        const listEl = type === 'guidelines' ? guidelineList : drawingList;
        listEl.innerHTML = '';

        files.forEach(f => {
            const filename = typeof f === 'string' ? f : f.filename;
            const li = document.createElement('li');

            const textSpan = document.createElement('span');
            textSpan.className = 'file-item-text';
            textSpan.textContent = filename;

            const delBtn = document.createElement('button');
            delBtn.className = 'delete-btn';
            delBtn.innerHTML = '✖';
            delBtn.onclick = () => deleteFile(type, filename);

            li.appendChild(textSpan);
            li.appendChild(delBtn);
            listEl.appendChild(li);
        });

        if (type === 'drawings') {
            analyzeBtn.disabled = files.length === 0;
            if (files.length > 0) {
                const first = files[0];
                currentDrawingFilename = first.filename;
                // Load pages for multi-page viewer
                allPageUrls = first.pages || [];
                pageDims    = [];  // not available from list endpoint, filled after analysis
                currentPageIdx = 0;
                showPage(0);
                document.querySelector('.empty-state').style.display = 'none';

                // NEW: Try to load existing results from the server
                tryLoadExistingResults(first.filename);
            } else {
                drawingImage.style.display = 'none';
                document.querySelector('.empty-state').style.display = 'flex';
                errorsTableBody.innerHTML = '<tr class="empty-row"><td colspan="3">No errors found.</td></tr>';
                markupLayer.innerHTML = '';
                errorCountBadge.textContent = '0 Issues';
                pageNav.style.display = 'none';
            }
        }
    }

    async function tryLoadExistingResults(filename) {
        try {
            const res = await fetch(`/api/results/${encodeURIComponent(filename)}`);
            if (res.ok) {
                const data = await res.json();
                console.info('[Persistence] Loading existing results for', filename);
                
                currentErrors = data.errors || [];
                markedUpPdfUrl = data.marked_up_pdf_url || '';
                
                // Update page URLs to use marked-up ones if available
                if (data.marked_up_pages) {
                    for (const [pIdx, url] of Object.entries(data.marked_up_pages)) {
                        allPageUrls[pIdx] = url;
                    }
                } else if (data.stem && data.page_count) {
                    allPageUrls = [];
                    for (let i = 0; i < data.page_count; i++) {
                        allPageUrls.push(`/drawings/${data.stem}_page_${i}.png`);
                    }
                }

                // Initial show once data is in
                showPage(0);
                renderErrors();
                
                // If there are results, show the Count Badge correctly
                errorCountBadge.textContent = `${currentErrors.length} Issues`;
            } else {
                // Clear any leftover state if no results found
                currentErrors = [];
                markedUpPdfUrl = '';
                renderErrors();
            }
        } catch (err) {
            console.warn('Failed to fetch existing results:', err);
        }
    }

    async function deleteFile(type, filename) {
        try {
            const encoded  = encodeURIComponent(filename);
            const endpoint = type === 'guidelines'
                ? `/api/delete_guideline/${encoded}`
                : `/api/delete_drawing/${encoded}`;
            await fetch(endpoint, { method: 'DELETE' });
            fetchFiles(type);
        } catch (err) {
            console.error(`Failed to delete ${filename}:`, err);
        }
    }

    fetchFiles('guidelines');
    fetchFiles('drawings');

    // ── Multi-page Viewer ────────────────────────────────────────────────
    function showPage(idx) {
        if (!allPageUrls.length) return;
        idx = Math.max(0, Math.min(idx, allPageUrls.length - 1));
        currentPageIdx = idx;

        drawingImage.src = allPageUrls[idx];
        drawingImage.style.display = 'block';

        // Show page nav only if >1 page
        if (allPageUrls.length > 1) {
            pageNav.style.display = 'flex';
            pageIndicator.textContent = `Page ${idx + 1} / ${allPageUrls.length}`;
            prevPageBtn.disabled = idx === 0;
            nextPageBtn.disabled = idx === allPageUrls.length - 1;
        } else {
            pageNav.style.display = 'none';
        }

        // Re-render markups for just this page
        renderErrors();
    }

    prevPageBtn.addEventListener('click', () => showPage(currentPageIdx - 1));
    nextPageBtn.addEventListener('click', () => showPage(currentPageIdx + 1));

    // ── Uploads ──────────────────────────────────────────────────────────
    guidelineInput.addEventListener('change', async (e) => {
        if (!e.target.files.length) return;
        guidelineStatus.innerHTML = 'Uploading...';
        guidelineStatus.style.color = '#3b82f6';

        const formData = new FormData();
        Array.from(e.target.files).forEach(file => formData.append('files', file));

        try {
            await fetch('/api/upload_guideline', { method: 'POST', body: formData });
            guidelineStatus.innerHTML = '✓ Uploaded successfully';
            guidelineStatus.style.color = '#10b981';
            fetchFiles('guidelines');
        } catch (err) {
            guidelineStatus.innerHTML = 'Upload failed';
            guidelineStatus.style.color = '#ef4444';
        }
        guidelineInput.value = '';
    });

    drawingInput.addEventListener('change', async (e) => {
        if (!e.target.files.length) return;
        drawingStatus.innerHTML = 'Uploading drawing...';
        drawingStatus.style.color = '#3b82f6';

        const formData = new FormData();
        Array.from(e.target.files).forEach(file => formData.append('file', file));

        try {
            const res  = await fetch('/api/upload_drawing', { method: 'POST', body: formData });
            const data = await res.json();
            drawingStatus.innerHTML = `✓ Uploaded (${data.page_count || 1} page(s))`;
            drawingStatus.style.color = '#10b981';
            fetchFiles('drawings');
        } catch (err) {
            drawingStatus.innerHTML = 'Upload failed';
            drawingStatus.style.color = '#ef4444';
        }
        drawingInput.value = '';
    });

    // ── Pipeline Step Tracker ────────────────────────────────────────────
    const STEP_IDS = ['step-1', 'step-2', 'step-3', 'step-4'];
    let currentStepIndex = -1;

    function resetPipelineSteps() {
        STEP_IDS.forEach(id => {
            document.getElementById(id).className = 'pipeline-step';
        });
        document.getElementById('analysis-live-msg').textContent = '';
    }

    function activateStep(idx) {
        if (currentStepIndex >= 0 && currentStepIndex < idx) {
            document.getElementById(STEP_IDS[currentStepIndex]).className = 'pipeline-step done';
        }
        currentStepIndex = idx;
        if (idx < STEP_IDS.length) {
            document.getElementById(STEP_IDS[idx]).className = 'pipeline-step active';
        }
    }

    function completeStep(idx) {
        if (idx >= 0 && idx < STEP_IDS.length) {
            document.getElementById(STEP_IDS[idx]).className = 'pipeline-step done';
        }
    }

    function setLiveMsg(msg) {
        document.getElementById('analysis-live-msg').textContent = msg;
    }

    // ── Analysis via WebSocket ───────────────────────────────────────────
    analyzeBtn.addEventListener('click', () => {
        if (!currentDrawingFilename) { alert('No drawing selected.'); return; }

        resetPipelineSteps();
        currentStepIndex = -1;
        loadingOverlay.classList.add('active');

        const protocol = window.location.protocol === 'https:' ? 'wss' : 'ws';
        const ws = new WebSocket(`${protocol}://${window.location.host}/ws/analyze`);

        ws.onopen = () => ws.send(JSON.stringify({ filename: currentDrawingFilename }));

        ws.onmessage = (event) => {
            const msg = JSON.parse(event.data);

            if (msg.status === 'step') {
                activateStep(detectStepNumber(msg.message));
                setLiveMsg(msg.message);

            } else if (msg.status === 'step_done') {
                completeStep(currentStepIndex);
                setLiveMsg(msg.message);

            } else if (msg.status === 'complete') {
                for (let i = currentStepIndex; i < STEP_IDS.length; i++) completeStep(i);
                setLiveMsg('✅ Analysis complete!');

                const data     = msg.data || {};
                currentErrors  = data.errors       || [];
                markedUpPdfUrl = data.marked_up_pdf_url || '';
                pageDims       = data.page_dims     || [];

                // Update page URLs if server sent stem + page_count
                if (data.stem && data.page_count) {
                    allPageUrls = [];
                    for (let i = 0; i < data.page_count; i++) {
                        allPageUrls.push(`/drawings/${data.stem}_page_${i}.png`);
                    }
                }

                if (data.executive_summary) {
                    console.info('[QC Summary]', data.executive_summary);
                }

        setTimeout(() => {
                    loadingOverlay.classList.remove('active');
                    document.querySelector('.empty-state').style.display = 'none';
                    drawingImage.style.display = 'block';
                    // Show page 0 with markups — wait for image to load first
                    if (drawingImage.complete && drawingImage.naturalWidth) {
                        positionMarkupLayer();
                        showPage(0);
                    } else {
                        drawingImage.onload = () => { positionMarkupLayer(); showPage(0); };
                    }
                }, 800);

            } else if (msg.status === 'error') {
                if (currentStepIndex >= 0) {
                    document.getElementById(STEP_IDS[currentStepIndex]).className = 'pipeline-step error';
                }
                setLiveMsg('❌ ' + msg.message);
                console.error('[WS Error]', msg.message);
                setTimeout(() => loadingOverlay.classList.remove('active'), 3000);
            }
        };

        ws.onerror = () => {
            setLiveMsg('❌ Connection error. Check server logs.');
            setTimeout(() => loadingOverlay.classList.remove('active'), 3000);
        };

        ws.onclose = () => console.log('WebSocket closed.');
    });

    function detectStepNumber(message) {
        if (message.includes('Step 1') || message.toLowerCase().includes('reading'))       return 0;
        if (message.includes('Step 2') || message.toLowerCase().includes('rules engine'))  return 1;
        if (message.includes('Step 3') || message.toLowerCase().includes('vision') || message.toLowerCase().includes('analyzing')) return 2;
        if (message.includes('Step 4') || message.toLowerCase().includes('markup'))        return 3;
        return Math.max(0, currentStepIndex);
    }

    // ── Render Errors + Markups ──────────────────────────────────────────
    function renderErrors() {
        errorsTableBody.innerHTML = '';
        markupLayer.innerHTML = '';

        // Filter errors to only those on the current page
        const pageErrors = currentErrors.filter(e => (e.page_index ?? 0) === currentPageIdx);
        const totalIssues = currentErrors.length;
        errorCountBadge.textContent = `${totalIssues} Issues`;

        if (totalIssues === 0) {
            errorsTableBody.innerHTML = '<tr class="empty-row"><td colspan="3">No errors found. All compliant!</td></tr>';
            return;
        }

        // Always show all errors in the table
        currentErrors.forEach((error, index) => {
            const onThisPage = (error.page_index ?? 0) === currentPageIdx;
            const tr = document.createElement('tr');
            tr.dataset.index = index;
            if (!onThisPage) tr.style.opacity = '0.4';

            const severityColor = error.severity === 'HIGH'
                ? '#ef4444' : error.severity === 'MEDIUM' ? '#f97316' : '#eab308';

            tr.innerHTML = `
                <td><strong>${error.id}</strong></td>
                <td><span class="category-tag">${error.category || 'General'}</span></td>
                <td>
                    <div style="font-weight:500">${error.error_message}</div>
                    <div style="font-size:0.78rem;color:#9094a6;margin-top:4px">
                        <span style="color:${severityColor};font-weight:600">${error.severity || ''}</span>
                        &nbsp;·&nbsp;Ref: ${error.standard_ref || 'N/A'}
                    </div>
                </td>
            `;
            errorsTableBody.appendChild(tr);

            // Only draw bounding boxes for errors on the current page
            if (!onThisPage) return;
            // Also skip if the backend indicated there is no bounding box
            if (error.has_bbox === false) return;

            // ── Coordinate Scaling ───────────────────────────────────────
            // Backend converted pct→pixels in full image space (img_w × img_h).
            // We just scale from full-image-pixels to the actual rendered image area.
            const { renderW, renderH } = getRenderedImageBounds();
            const imgW   = error.img_w || drawingImage.naturalWidth  || renderW;
            const imgH   = error.img_h || drawingImage.naturalHeight || renderH;
            const scaleX = renderW / imgW;
            const scaleY = renderH / imgH;

            const sx = error.x      * scaleX;
            const sy = error.y      * scaleY;
            const sw = Math.max(error.width  * scaleX, 24);
            const sh = Math.max(error.height * scaleY, 24);

            const box = document.createElement('div');
            box.className = 'bounding-box';
            box.dataset.index = index;

            // Color by result type: High is always red, Review is orange (if not High)
            const isReview = (error.standard_ref || '').includes('REVIEW');
            if (error.severity === 'HIGH') {
                box.classList.remove('review'); // Default is red
            } else if (isReview) {
                box.classList.add('review');
            }

            box.style.left   = `${sx}px`;
            box.style.top    = `${sy}px`;
            box.style.width  = `${Math.max(sw, 20)}px`;
            box.style.height = `${Math.max(sh, 20)}px`;

            const label = document.createElement('div');
            label.className = 'markup-label';
            label.textContent = error.id;
            box.appendChild(label);

            markupLayer.appendChild(box);

            // Hover sync
            const activate = () => {
                document.querySelectorAll('.bounding-box').forEach(b => b.classList.remove('active'));
                document.querySelectorAll('#errors-table tr').forEach(r => r.classList.remove('active'));
                box.classList.add('active');
                tr.classList.add('active');
                tr.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
            };
            const deactivate = () => {
                box.classList.remove('active');
                tr.classList.remove('active');
            };

            tr.addEventListener('mouseenter', activate);
            tr.addEventListener('mouseleave', deactivate);
            box.addEventListener('mouseenter', activate);
            box.addEventListener('mouseleave', deactivate);

            // Click on table row → jump to that page
            tr.addEventListener('click', () => {
                const targetPage = error.page_index ?? 0;
                if (targetPage !== currentPageIdx) showPage(targetPage);
            });
        });

        updateMarkupLayerSize();
    }

    // ── Correct rendered-image bounds (object-fit: contain aware) ───────
    // <img> getBoundingClientRect() returns the ELEMENT size, not the actual
    // rendered image area. With object-fit:contain, blank bars are added on
    // top/bottom or sides. We must compute the true rendered rect.
    function getRenderedImageBounds() {
        const el   = drawingImage;
        const elW  = el.offsetWidth;
        const elH  = el.offsetHeight;
        const natW = el.naturalWidth  || elW;
        const natH = el.naturalHeight || elH;

        const elRatio  = elW / elH;
        const natRatio = natW / natH;

        let renderW, renderH, offsetX, offsetY;
        if (natRatio > elRatio) {
            // Image is wider → fill width, bars on top & bottom
            renderW  = elW;
            renderH  = elW / natRatio;
            offsetX  = 0;
            offsetY  = (elH - renderH) / 2;
        } else {
            // Image is taller → fill height, bars on left & right
            renderH  = elH;
            renderW  = elH * natRatio;
            offsetX  = (elW - renderW) / 2;
            offsetY  = 0;
        }
        return { renderW, renderH, offsetX, offsetY };
    }

    function positionMarkupLayer() {
        if (!drawingImage.naturalWidth) return;
        const { renderW, renderH, offsetX, offsetY } = getRenderedImageBounds();
        markupLayer.style.width     = `${renderW}px`;
        markupLayer.style.height    = `${renderH}px`;
        markupLayer.style.left      = `${offsetX}px`;
        markupLayer.style.top       = `${offsetY}px`;
        markupLayer.style.transform = 'none';
    }

    // Re-render on resize
    window.addEventListener('resize', () => {
        positionMarkupLayer();
        if (currentErrors.length > 0) renderErrors();
    });

    // Re-render when page image finishes loading (needed for correct BoundingClientRect)
    drawingImage.addEventListener('load', () => {
        positionMarkupLayer();
        if (currentErrors.length > 0) renderErrors();
    });

    // ── Drag and Drop ────────────────────────────────────────────────────
    document.querySelectorAll('.drop-zone').forEach(zone => {
        zone.addEventListener('dragover', (e) => { e.preventDefault(); zone.classList.add('dragover'); });
        zone.addEventListener('dragleave', () => zone.classList.remove('dragover'));
        zone.addEventListener('drop', (e) => {
            e.preventDefault();
            zone.classList.remove('dragover');
            const input = zone.querySelector('input[type="file"]');
            if (e.dataTransfer.files.length) {
                input.files = e.dataTransfer.files;
                input.dispatchEvent(new Event('change', { bubbles: true }));
            }
        });
    });

    // ── Export Report ────────────────────────────────────────────────────
    document.getElementById('download-btn').addEventListener('click', () => {
        if (markedUpPdfUrl) {
            window.open(markedUpPdfUrl, '_blank');
        } else {
            alert('Please run the AI inspection first to generate the report.');
        }
    });

    // ── Zoom and Pan ─────────────────────────────────────────────────────
    const zoomWrapper = document.getElementById('zoom-wrapper');
    const zoomControls = document.getElementById('zoom-controls');
    let scale = 1;
    let pointX = 0;
    let pointY = 0;
    let isPanning = false;
    let startX = 0;
    let startY = 0;

    function applyTransform() {
        zoomWrapper.style.transform = `translate(${pointX}px, ${pointY}px) scale(${scale})`;
    }

    function resetZoom() {
        scale = 1; pointX = 0; pointY = 0;
        applyTransform();
    }

    document.getElementById('zoom-in-btn').addEventListener('click', () => {
        scale = Math.min(scale * 1.2, 5);
        applyTransform();
    });

    document.getElementById('zoom-out-btn').addEventListener('click', () => {
        scale = Math.max(scale / 1.2, 0.5);
        if (scale === 0.5) { pointX = 0; pointY = 0; }
        applyTransform();
    });

    document.getElementById('zoom-reset-btn').addEventListener('click', resetZoom);

    zoomWrapper.addEventListener('wheel', (e) => {
        e.preventDefault();
        const xs = (e.clientX - pointX) / scale;
        const ys = (e.clientY - pointY) / scale;
        const delta = (e.wheelDelta ? e.wheelDelta : -e.deltaY);
        (delta > 0) ? (scale *= 1.1) : (scale /= 1.1);
        scale = Math.min(Math.max(0.5, scale), 10);
        pointX = e.clientX - xs * scale;
        pointY = e.clientY - ys * scale;
        applyTransform();
    });

    zoomWrapper.addEventListener('mousedown', (e) => {
        e.preventDefault();
        startX = e.clientX - pointX;
        startY = e.clientY - pointY;
        isPanning = true;
    });

    window.addEventListener('mousemove', (e) => {
        if (!isPanning) return;
        pointX = e.clientX - startX;
        pointY = e.clientY - startY;
        applyTransform();
    });

    window.addEventListener('mouseup', () => {
        isPanning = false;
    });

    // Show controls when image is loaded
    drawingImage.addEventListener('load', () => {
        zoomControls.style.display = 'flex';
        resetZoom();
    });
});
