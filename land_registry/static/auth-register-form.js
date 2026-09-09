/* Registration-page adapter.  Clerk's browser bundle may be present without
 * its optional UI components; in that case keep the local registration form
 * usable instead of logging a misleading hard error or leaving a spinner. */
(function () {
    'use strict';

    let config;
    let clerkEnabled = false;
    let afterSignUpUrl = '/map';
    let registerPostUrl = '/auth/register';

    function showFallback() {
        document.getElementById('clerk-loading')?.remove();
        const form = document.getElementById('fallback-register-form') || document.getElementById('registerForm');
        if (form) {
            form.style.display = '';
            form.removeAttribute('hidden');
        }
    }

    function waitForClerk() {
        return new Promise(resolve => {
            let attempts = 0;
            const timer = setInterval(() => {
                if (window.Clerk) { clearInterval(timer); resolve(true); }
                else if (++attempts >= 80) { clearInterval(timer); resolve(false); }
            }, 100);
        });
    }

    function bindForm() {
        const form = document.getElementById(clerkEnabled ? 'fallback-register-form' : 'registerForm');
        if (!form) return;
        form.addEventListener('submit', async event => {
            event.preventDefault();
            if (!form.checkValidity()) { form.classList.add('was-validated'); return; }
            const password = form.querySelector('#password');
            const confirm = form.querySelector('#confirmPassword');
            if (password && confirm && password.value !== confirm.value) {
                confirm.setCustomValidity('Passwords do not match');
                form.classList.add('was-validated');
                return;
            }
            confirm?.setCustomValidity('');
            const submit = form.querySelector('button[type="submit"]');
            const error = document.getElementById('registerError');
            const errorText = document.getElementById('registerErrorText');
            submit && (submit.disabled = true);
            error?.classList.add('d-none');
            try {
                const response = await fetch(registerPostUrl, {
                    method: 'POST',
                    headers: { Accept: 'application/json', 'X-Requested-With': 'XMLHttpRequest' },
                    body: new FormData(form),
                });
                if (!response.ok) {
                    const data = await response.json().catch(() => ({}));
                    throw new Error(data.detail || 'Failed to create account');
                }
                document.getElementById('registerSuccess')?.classList.remove('d-none');
                form.reset();
                setTimeout(() => { window.location.href = afterSignUpUrl; }, 1500);
            } catch (err) {
                if (errorText) errorText.textContent = err.message;
                error?.classList.remove('d-none');
            } finally {
                submit && (submit.disabled = false);
            }
        });
    }

    async function init() {
        config = document.getElementById('authRegisterConfig');
        if (!config) return;
        clerkEnabled = config.dataset.clerkEnabled === 'true';
        afterSignUpUrl = config.dataset.afterSignUpUrl || '/map';
        registerPostUrl = config.dataset.registerPostUrl || '/auth/register';
        bindForm();
        if (!clerkEnabled) return;
        if (!await waitForClerk()) { showFallback(); return; }
        try {
            await window.Clerk.load();
            if (window.Clerk.user) { window.location.replace(afterSignUpUrl); return; }
            const mountTarget = document.getElementById('clerk-sign-up');
            if (!mountTarget || typeof window.Clerk.mountSignUp !== 'function') {
                showFallback();
                return;
            }
            document.getElementById('clerk-loading')?.remove();
            window.Clerk.mountSignUp(mountTarget, {
                signInUrl: config.dataset.signInUrl || '/auth/login',
                forceRedirectUrl: afterSignUpUrl,
                signInForceRedirectUrl: config.dataset.afterSignInUrl || '/map',
            });
        } catch (error) {
            // A missing Clerk UI package is an expected deployment fallback,
            // not an application error: the local form remains available.
            console.info('Clerk UI unavailable; using local registration form.');
            showFallback();
        }
    }

    window.addEventListener('DOMContentLoaded', init);
})();
