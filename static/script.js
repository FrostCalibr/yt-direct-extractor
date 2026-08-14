document.addEventListener('DOMContentLoaded', () => {
    const form = document.getElementById('extract-form');
    const urlInput = document.getElementById('url-input');
    const submitBtn = document.getElementById('submit-btn');
    const btnText = submitBtn.querySelector('.btn-text');
    const spinner = submitBtn.querySelector('.spinner');
    const pasteBtn = document.getElementById('paste-btn');

    const errorBox = document.getElementById('error-box');
    const errorMessage = document.getElementById('error-message');

    const resultCard = document.getElementById('result-card');
    const videoThumbnail = document.getElementById('video-thumbnail');
    const videoDuration = document.getElementById('video-duration');
    const videoTitle = document.getElementById('video-title');
    const videoUploader = document.getElementById('video-uploader');
    const videoViews = document.getElementById('video-views');

    const combinedStreamsList = document.getElementById('combined-streams-list');
    const audioStreamsList = document.getElementById('audio-streams-list');

    pasteBtn.addEventListener('click', async () => {
        try {
            const text = await navigator.clipboard.readText();
            if (text) {
                urlInput.value = text;
                urlInput.focus();
            }
        } catch (err) {
            console.log('Clipboard permission denied');
        }
    });

    form.addEventListener('submit', async (e) => {
        e.preventDefault();
        const url = urlInput.value.trim();

        if (!url) return;

        showLoading(true);
        hideError();
        resultCard.classList.add('hidden');

        try {
            const response = await fetch('/api/extract', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: url })
            });

            const data = await response.json();

            if (!response.ok) {
                throw new Error(data.detail || 'Extraction failed on server.');
            }

            renderResults(data);
        } catch (err) {
            showError(err.message || 'Direct link extraction failed.');
        } finally {
            showLoading(false);
        }
    });

    function showLoading(isLoading) {
        if (isLoading) {
            submitBtn.disabled = true;
            btnText.classList.add('hidden');
            spinner.classList.remove('hidden');
        } else {
            submitBtn.disabled = false;
            btnText.classList.remove('hidden');
            spinner.classList.add('hidden');
        }
    }

    function showError(msg) {
        errorMessage.textContent = msg;
        errorBox.classList.remove('hidden');
    }

    function hideError() {
        errorBox.classList.add('hidden');
    }

    function renderResults(data) {
        videoThumbnail.src = data.thumbnail || 'https://via.placeholder.com/320x180?text=No+Thumbnail';
        videoDuration.textContent = data.duration || '';
        videoTitle.textContent = data.title;
        videoUploader.textContent = `👤 ${data.uploader}`;
        videoViews.textContent = `👁️ ${data.view_count}`;

        combinedStreamsList.innerHTML = '';
        if (data.streams.combined && data.streams.combined.length > 0) {
            data.streams.combined.forEach(stream => {
                combinedStreamsList.appendChild(createStreamPill(stream));
            });
        } else {
            combinedStreamsList.innerHTML = '<div class="stream-meta">No combined video+audio streams found.</div>';
        }

        audioStreamsList.innerHTML = '';
        if (data.streams.audio && data.streams.audio.length > 0) {
            data.streams.audio.forEach(stream => {
                audioStreamsList.appendChild(createStreamPill(stream));
            });
        } else {
            audioStreamsList.innerHTML = '<div class="stream-meta">No standalone audio streams found.</div>';
        }

        resultCard.classList.remove('hidden');
        resultCard.scrollIntoView({ behavior: 'smooth', block: 'nearest' });
    }

    function createStreamPill(stream) {
        const pill = document.createElement('div');
        pill.className = 'stream-pill';

        const details = document.createElement('div');
        details.className = 'stream-details';

        const quality = document.createElement('div');
        quality.className = 'stream-quality';
        quality.textContent = `${stream.quality} (${stream.ext.toUpperCase()})`;

        const meta = document.createElement('div');
        meta.className = 'stream-meta';
        meta.textContent = `Size: ${stream.filesize}`;

        details.appendChild(quality);
        details.appendChild(meta);

        const downloadLink = document.createElement('a');
        downloadLink.className = 'download-link';
        downloadLink.href = stream.direct_url;
        downloadLink.target = '_blank';
        downloadLink.rel = 'noopener noreferrer';
        downloadLink.setAttribute('download', '');
        downloadLink.innerHTML = '⚡ Direct CDN Link';

        pill.appendChild(details);
        pill.appendChild(downloadLink);

        return pill;
    }
});
