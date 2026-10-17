/**
 * Haptics module for Android + iOS with graceful fallbacks.
 * No dependencies. Feature-detects everything.
 */
const Haptics = (() => {
    let audioCtx = null;
    let muted = false;
    let unlocked = false;
    let iosSwitchEl = null;

    // Vibration patterns (ms)
    const patterns = {
        tap: [10],
        success: [20, 40, 20],
        error: [60, 40, 60, 40, 60],
        alert: [200, 100, 200, 100, 400],
    };

    // Check reduced motion preference
    const prefersReducedMotion = () =>
        window.matchMedia('(prefers-reduced-motion: reduce)').matches;

    // Android/Chrome vibration
    const canVibrate = () => typeof navigator.vibrate === 'function';

    // iOS switch haptic trick: create a hidden checkbox with switch attribute
    const createIOSSwitch = () => {
        if (iosSwitchEl) return;
        const input = document.createElement('input');
        input.type = 'checkbox';
        input.setAttribute('switch', '');
        input.style.cssText = 'position:fixed;top:-9999px;left:-9999px;opacity:0;pointer-events:none;';
        input.id = 'ios-haptic-switch';
        const label = document.createElement('label');
        label.htmlFor = 'ios-haptic-switch';
        label.style.cssText = 'position:fixed;top:-9999px;left:-9999px;';
        document.body.appendChild(input);
        document.body.appendChild(label);
        iosSwitchEl = { input, label };
    };

    // iOS haptic via switch toggle (only works in user gesture)
    const iosHaptic = () => {
        if (!iosSwitchEl) createIOSSwitch();
        try {
            iosSwitchEl.label.click();
        } catch (_) { /* ignore */ }
    };

    // Check if iOS (for fallback decisions)
    const isIOS = () => /iPad|iPhone|iPod/.test(navigator.userAgent) && !window.MSStream;

    // Create AudioContext for iOS beep fallback
    const getAudioCtx = () => {
        if (audioCtx) return audioCtx;
        try {
            audioCtx = new (window.AudioContext || window.webkitAudioContext)();
        } catch (_) { /* unsupported */ }
        return audioCtx;
    };

    // Play a short beep for iOS non-gesture alerts
    const beep = (freq = 880, duration = 80) => {
        const ctx = getAudioCtx();
        if (!ctx || muted) return;
        try {
            const osc = ctx.createOscillator();
            const gain = ctx.createGain();
            osc.connect(gain);
            gain.connect(ctx.destination);
            osc.frequency.value = freq;
            gain.gain.value = 0.15;
            osc.start();
            osc.stop(ctx.currentTime + duration / 1000);
        } catch (_) { /* ignore */ }
    };

    // Screen flash for iOS fallback (respects reduced motion)
    const flash = (color = 'rgba(255,0,0,0.3)') => {
        if (prefersReducedMotion() || muted) return;
        const el = document.createElement('div');
        el.style.cssText = `
            position:fixed;inset:0;background:${color};pointer-events:none;z-index:99999;
            animation:haptic-flash 200ms ease-out forwards;
        `;
        document.body.appendChild(el);
        setTimeout(() => el.remove(), 250);
    };

    // Inject flash animation CSS
    const injectStyles = () => {
        if (document.getElementById('haptics-styles')) return;
        const style = document.createElement('style');
        style.id = 'haptics-styles';
        style.textContent = `
            @keyframes haptic-flash {
                0% { opacity: 1; }
                100% { opacity: 0; }
            }
        `;
        document.head.appendChild(style);
    };

    /**
     * Trigger haptic feedback.
     * @param {string} type - 'tap' | 'success' | 'error' | 'alert'
     * @param {boolean} inGesture - true if called from a user gesture (click/tap)
     */
    const trigger = (type, inGesture = false) => {
        if (muted) return;
        const pattern = patterns[type] || patterns.tap;

        // Android: use Vibration API
        if (canVibrate()) {
            try {
                navigator.vibrate(pattern);
                return;
            } catch (_) { /* fallback */ }
        }

        // iOS: try switch haptic in gesture, otherwise beep + flash
        if (isIOS()) {
            if (inGesture) {
                iosHaptic();
            } else {
                // No vibration outside gesture on iOS - use audio + visual
                if (type === 'alert') {
                    beep(440, 150);
                    setTimeout(() => beep(440, 150), 200);
                    flash('rgba(255,0,0,0.25)');
                } else if (type === 'success') {
                    beep(880, 100);
                    flash('rgba(0,200,100,0.2)');
                } else if (type === 'error') {
                    beep(220, 200);
                    flash('rgba(255,0,0,0.2)');
                }
            }
        }
    };

    // Unlock AudioContext (must be called from user gesture)
    const unlock = () => {
        const ctx = getAudioCtx();
        if (ctx && ctx.state === 'suspended') {
            ctx.resume();
        }
        createIOSSwitch();
        unlocked = true;
        // Test haptic
        trigger('tap', true);
    };

    const setMuted = (val) => { muted = !!val; };
    const isMuted = () => muted;
    const isUnlocked = () => unlocked;

    // Initialize on load
    if (typeof document !== 'undefined') {
        if (document.readyState === 'loading') {
            document.addEventListener('DOMContentLoaded', injectStyles);
        } else {
            injectStyles();
        }
    }

    return {
        trigger,
        unlock,
        setMuted,
        isMuted,
        isUnlocked,
        patterns,
    };
})();

// Export for module systems
if (typeof module !== 'undefined' && module.exports) {
    module.exports = Haptics;
}
