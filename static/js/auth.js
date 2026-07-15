// Login & Register modal handling
$(function() {
    // Toggle password visibility
    $(document).on('click', '.toggle-pw', function() {
        var targetId = $(this).data('target');
        var input = $('#' + targetId);
        var icon = $(this).find('i');
        if (input.attr('type') === 'password') {
            input.attr('type', 'text');
            icon.removeClass('fa-eye').addClass('fa-eye-slash');
        } else {
            input.attr('type', 'password');
            icon.removeClass('fa-eye-slash').addClass('fa-eye');
        }
    });

    // --- Consolidated Login Handling ---
    $(document).on('click', '.toggle-login-mode', function() {
        $('#passwordSection, #otpSection').toggleClass('d-none');
        const isOtpMode = !$('#otpSection').hasClass('d-none');
        $(this).text(isOtpMode ? 'Login with Password' : 'Login with OTP');
        
        // Update requirements
        $('#loginPassword').prop('required', !isOtpMode);
        $('#loginOtp').prop('required', isOtpMode);
    });

    // Reset modals on close
    $('#loginModal').on('show.bs.modal', function() {
        const urlParams = new URLSearchParams(window.location.search);
        let next = urlParams.get('next');
        if (!next) {
            // Use current page as default redirect after login
            next = window.location.pathname + window.location.search;
        }
        $(this).find('input[name="next"]').val(next);
    });

    $('#loginModal').on('hidden.bs.modal', function() {
        $('#loginModalForm')[0].reset();
        $('#passwordSection').removeClass('d-none');
        $('#otpSection').addClass('d-none');
        $('#loginAlert').addClass('d-none').removeClass('alert-success alert-danger').text('');
        $('#loginBtnText').removeClass('d-none');
        $('#loginBtnSpinner').addClass('d-none');
        $('#loginSubmitBtn').prop('disabled', false);
        
        // Reset timers
        if (unifiedOtpCountdown) clearInterval(unifiedOtpCountdown);
        $('#sendUnifiedOtpBtn').prop('disabled', false).text('Send OTP');
        $('#unifiedOtpTimer').addClass('d-none');
    });

    $('#registerModal').on('hidden.bs.modal', function() {
        $('#registerModalForm')[0].reset();
        $('#registerAlert').addClass('d-none').removeClass('alert-success alert-danger').text('');
        $('#registerBtnText').removeClass('d-none');
        $('#registerBtnSpinner').addClass('d-none');
        $('#registerSubmitBtn').prop('disabled', false);
    });

    // Unified OTP Logic
    let unifiedOtpCountdown;
    function startUnifiedOtpTimer() {
        let seconds = 60;
        $('#sendUnifiedOtpBtn').prop('disabled', true);
        $('#unifiedOtpTimer').removeClass('d-none');
        $('#unifiedTimerCount').text(seconds);
        
        if (unifiedOtpCountdown) clearInterval(unifiedOtpCountdown);
        unifiedOtpCountdown = setInterval(function() {
            seconds--;
            $('#unifiedTimerCount').text(seconds);
            if (seconds <= 0) {
                clearInterval(unifiedOtpCountdown);
                $('#sendUnifiedOtpBtn').prop('disabled', false).text('Resend OTP');
                $('#unifiedOtpTimer').addClass('d-none');
            }
        }, 1000);
    }

    $('#sendUnifiedOtpBtn').on('click', function() {
        const identifier = $('#loginUsername').val();
        if (!identifier) {
            showToast('error', 'Please enter your email or mobile number.');
            return;
        }

        const isEmail = identifier.includes('@');
        const endpoint = isEmail ? '/send-login-otp' : '/send-whatsapp-otp';
        const data = isEmail ? { email: identifier } : { mobile: identifier };
        data.csrf_token = $('input[name="csrf_token"]').val();

        const $btn = $(this);
        const originalText = $btn.text();
        $btn.prop('disabled', true).text('Sending...');

        $.ajax({
            url: endpoint,
            method: 'POST',
            data: data,
            success: function(res) {
                if (res.success) {
                    showToast('success', res.message || 'OTP sent successfully!');
                    startUnifiedOtpTimer();
                } else {
                    showToast('error', res.message || 'Failed to send OTP.');
                    $btn.prop('disabled', false).text(originalText);
                }
            },
            error: function(xhr) {
                let msg = 'Failed to send OTP. Please try again.';
                if (xhr.responseJSON && xhr.responseJSON.message) msg = xhr.responseJSON.message;
                showToast('error', msg);
                $btn.prop('disabled', false).text(originalText);
            }
        });
    });

    $('#otpVerifySubmitBtn').on('click', function() {
        const identifier = $('#loginUsername').val();
        const otp = $('#loginOtp').val();
        if (!otp) {
            showToast('error', 'Please enter the 6-digit OTP.');
            return;
        }

        const isEmail = identifier.includes('@');
        const endpoint = isEmail ? '/verify-login-otp' : '/verify-whatsapp-otp';
        const data = isEmail ? 
            { email: identifier, otp: otp } : 
            { mobile: identifier, otp: otp };
        data.next = $('#loginModalForm input[name="next"]').val();
        data.csrf_token = $('input[name="csrf_token"]').val();

        const $btn = $(this);
        const originalHtml = $btn.html();
        $btn.prop('disabled', true).html('<i class="fas fa-spinner fa-spin mr-1"></i> Verifying...');

        $.ajax({
            url: endpoint,
            method: 'POST',
            data: data,
            success: function(res) {
                if (res.success) {
                    showToast('success', 'Login successful!');
                    setTimeout(function() { 
                        if (res.next) window.location.href = res.next;
                        else location.reload(); 
                    }, 800);
                } else {
                    showToast('error', res.message || 'Invalid OTP.');
                    $btn.prop('disabled', false).html(originalHtml);
                }
            },
            error: function(xhr) {
                let msg = 'Verification failed. Please try again.';
                if (xhr.responseJSON && xhr.responseJSON.message) msg = xhr.responseJSON.message;
                showToast('error', msg);
                $btn.prop('disabled', false).html(originalHtml);
            }
        });
    });

    // Password Login AJAX
    $('#loginModalForm').on('submit', function(e) {
        if (!$('#otpSection').hasClass('d-none')) {
            e.preventDefault();
            $('#otpVerifySubmitBtn').click();
            return;
        }
        
        e.preventDefault();
        var $btn = $('#loginSubmitBtn');
        $('#loginBtnText').addClass('d-none');
        $('#loginBtnSpinner').removeClass('d-none');
        $btn.prop('disabled', true);
        
        $.ajax({
            url: "/login",
            method: 'POST',
            data: $(this).serialize(),
            headers: {'X-Requested-With': 'XMLHttpRequest'},
            success: function(resp) {
                if (resp.success) {
                    $('#loginAlert').removeClass('d-none alert-danger').addClass('alert-success').html('<i class="fas fa-check-circle mr-1"></i>' + (resp.message || 'Login successful!'));
                    setTimeout(function() { 
                        if (resp.next) window.location.href = resp.next;
                        else location.reload(); 
                    }, 900);
                } else {
                    $('#loginAlert').removeClass('d-none alert-success').addClass('alert-danger').html('<i class="fas fa-exclamation-circle mr-1"></i>' + (resp.error || 'Invalid credentials.'));
                    $('#loginBtnText').removeClass('d-none');
                    $('#loginBtnSpinner').addClass('d-none');
                    $btn.prop('disabled', false);
                }
            },
            error: function() {
                $('#loginAlert').removeClass('d-none alert-success').addClass('alert-danger').html('<i class="fas fa-exclamation-circle mr-1"></i>An error occurred. Please try again.');
                $('#loginBtnText').removeClass('d-none');
                $('#loginBtnSpinner').addClass('d-none');
                $btn.prop('disabled', false);
            }
        });
    });

    // Register AJAX
    $('#registerModalForm').on('submit', function(e) {
        e.preventDefault();
        var $btn = $('#registerSubmitBtn');
        $('#registerBtnText').addClass('d-none');
        $('#registerBtnSpinner').removeClass('d-none');
        $btn.prop('disabled', true);

        $.ajax({
            url: '/register',
            method: 'POST',
            data: $(this).serialize(),
            headers: {'X-Requested-With': 'XMLHttpRequest'},
            success: function(res) {
                if (res.success) {
                    $('#registerModal').modal('hide');
                    showToast('success', res.message || 'Registration successful! Please login.');
                    setTimeout(function() { $('#loginModal').modal('show'); }, 1000);
                } else {
                    showToast('error', res.message || 'Registration failed. Please try again.');
                    $('#registerBtnText').removeClass('d-none');
                    $('#registerBtnSpinner').addClass('d-none');
                    $btn.prop('disabled', false);
                }
            },
            error: function(xhr) {
                let msg = 'Registration failed. Please try again.';
                if (xhr.responseJSON && xhr.responseJSON.message) msg = xhr.responseJSON.message;
                showToast('error', msg);
                $('#registerBtnText').removeClass('d-none');
                $('#registerBtnSpinner').addClass('d-none');
                $btn.prop('disabled', false);
            }
        });
    });

    // Forgot Password AJAX
    $('#forgotPasswordModal').on('hidden.bs.modal', function() {
        $('#forgotPasswordModalForm')[0].reset();
        $('#forgotBtnText').removeClass('d-none');
        $('#forgotBtnSpinner').addClass('d-none');
        $('#forgotSubmitBtn').prop('disabled', false);
    });

    $('#forgotPasswordModalForm').on('submit', function(e) {
        e.preventDefault();
        var $btn = $('#forgotSubmitBtn');
        $('#forgotBtnText').addClass('d-none');
        $('#forgotBtnSpinner').removeClass('d-none');
        $btn.prop('disabled', true);

        $.ajax({
            url: '/forgot-password',
            method: 'POST',
            data: $(this).serialize(),
            headers: {'X-Requested-With': 'XMLHttpRequest'},
            success: function(res) {
                if (res.success) {
                    $('#forgotPasswordModal').modal('hide');
                    showToast('success', res.message || 'Password reset link sent!');
                    setTimeout(function() { $('#loginModal').modal('show'); }, 1000);
                } else {
                    showToast('error', res.message || 'Failed to send reset link.');
                    $('#forgotBtnText').removeClass('d-none');
                    $('#forgotBtnSpinner').addClass('d-none');
                    $btn.prop('disabled', false);
                }
            },
            error: function(xhr) {
                let msg = 'Error processing request. Please try again.';
                if (xhr.responseJSON && xhr.responseJSON.message) msg = xhr.responseJSON.message;
                showToast('error', msg);
                $('#forgotBtnText').removeClass('d-none');
                $('#forgotBtnSpinner').addClass('d-none');
                $btn.prop('disabled', false);
            }
        });
    });

    // Trigger login modal if requested in URL (e.g. from redirect)
    const urlParams = new URLSearchParams(window.location.search);
    if (urlParams.get('login') === 'true' && !window.AANYAAS.userLoggedIn) {
        $('#loginModal').modal('show');
    }
});
