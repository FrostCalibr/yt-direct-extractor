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
            // First: Call backend server endpoint
            const response = await fetch('/api/extract', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json' },
                body: JSON.stringify({ url: url })
            });

            const data = await response.json();

            if (response.ok && data.client_fallback) {
                console.log('Datacenter IP flagged on server. Running residential in-browser extraction...');
                const clientData = await performResidentialExtraction(url, data.video_id);
                renderResults(clientData);
            } else if (response.ok && data.streams) {
                renderResults(data);
            } else {
                // If server failed completely, perform residential in-browser extraction immediately
                const videoId = extractVideoId(url);
                const clientData = await performResidentialExtraction(url, videoId);
                renderResults(clientData);
            }
        } catch (err) {
            console.log('Server unreachable, executing client-side extraction:', err);
            try {
                const videoId = extractVideoId(url);
                const clientData = await performResidentialExtraction(url, videoId);
                renderResults(clientData);
            } catch (clientErr) {
                showError(clientErr.message || 'Direct link extraction failed.');
            }
        } finally {
            showLoading(false);
        }
    });

    function extractVideoId(url) {
        const match = url.match(/(?:v=|\/)([0-9A-Za-z_-]{11})/);
        return match ? match[1] : null;
    }

    async function performResidentialExtraction(videoUrl, videoId) {
        // Method A: Direct Cobalt API call from residential browser
        try {
            console.log('Attempting in-browser Cobalt extraction for:', videoUrl);
            const cobaltRes = await fetch('https://api.cobalt.tools/', {
                method: 'POST',
                headers: {
                    'Content-Type': 'application/json',
                    'Accept': 'application/json'
                },
                body: JSON.stringify({
                    url: videoUrl,
                    videoQuality: '720'
                })
            });

            if (cobaltRes.ok) {
                const data = await cobaltRes.json();
                if (data.url) {
                    return {
                        success: true,
                        title: `YouTube Video (${videoId || 'Direct'})`,
                        thumbnail: videoId ? `https://i.ytimg.com/vi/${videoId}/hqdefault.jpg` : '',
                        duration: 'High Speed Stream',
                        uploader: 'YouTube',
                        view_count: 'Direct CDN',
                        streams: {
                            combined: [{
                                format_id: 'cobalt_browser',
                                quality: 'HD Direct Stream (Cobalt)',
                                height: 720,
                                ext: 'mp4',
                                filesize: 'High Speed',
                                direct_url: data.url,
                                type: 'video_audio'
                            }],
                            audio: []
                        }
                    };
                }
            }
        } catch (e) {
            console.log('In-browser Cobalt call failed:', e);
        }

        // Method B: Direct Invidious API call from residential browser
        if (videoId) {
            const invidiousInstances = [
                `https://inv.tux.pizza/api/v1/videos/${videoId}`,
                `https://invidious.privacydev.net/api/v1/videos/${videoId}`,
                `https://invidious.nerdvpn.de/api/v1/videos/${videoId}`
            ];

            for (const endpoint of invidiousInstances) {
                try {
                    console.log('Attempting in-browser Invidious extraction via:', endpoint);
                    const res = await fetch(endpoint);
                    if (res.ok) {
                        const data = await res.json();
                        const combined = (data.formatStreams || []).map(f => ({
                            format_id: 'browser_invidious',
                            quality: f.qualityLabel || '720p',
                            ext: f.container || 'mp4',
                            filesize: 'Direct CDN Link',
                            direct_url: f.url,
                            type: 'video_audio'
                        }));

                        const audio = (data.adaptiveFormats || [])
                            .filter(a => (a.type || '').includes('audio'))
                            .map(a => ({
                                format_id: 'browser_audio',
                                quality: a.bitrate ? `${Math.round(a.bitrate / 1000)} kbps` : 'Audio',
                                ext: a.container || 'm4a',
                                filesize: 'Direct CDN Link',
                                direct_url: a.url,
                                type: 'audio_only'
                            }));

                        return {
                            success: true,
                            title: data.title || 'YouTube Video',
                            thumbnail: data.videoThumbnails ? data.videoThumbnails[0].url : `https://i.ytimg.com/vi/${videoId}/hqdefault.jpg`,
                            duration: data.lengthSeconds ? `${Math.floor(data.lengthSeconds / 60)}:${data.lengthSeconds % 60}` : 'N/A',
                            uploader: data.author || 'YouTube Creator',
                            view_count: data.viewCount ? data.viewCount.toLocaleString() : 'N/A',
                            streams: {
                                combined: combined.slice(0, 4),
                                audio: audio.slice(0, 3)
                            }
                        };
                    }
                } catch (err) {
                    console.log('In-browser Invidious endpoint failed:', endpoint, err);
                }
            }
        }

        throw new Error('Residential extraction failed. Please ensure the YouTube URL is valid and public.');
    }

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
        videoViews.textContent = `👁️ ${data.view_count} views`;

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
