(function ($) {
    "use strict";

    // Dropdown on mouse hover
    $(document).ready(function () {
        function toggleNavbarMethod() {
            if ($(window).width() > 992) {
                $('.navbar .dropdown').on('mouseover', function () {
                    $('.dropdown-toggle', this).trigger('click');
                }).on('mouseout', function () {
                    $('.dropdown-toggle', this).trigger('click').blur();
                });
            } else {
                $('.navbar .dropdown').off('mouseover').off('mouseout');
            }
        }
        toggleNavbarMethod();
        $(window).resize(toggleNavbarMethod);
    });

    // Back to top button
    $(window).scroll(function () {
        if ($(this).scrollTop() > 100) {
            $('.back-to-top').fadeIn('slow');
        } else {
            $('.back-to-top').fadeOut('slow');
        }
    });
    $('.back-to-top').click(function () {
        $('html, body').animate({scrollTop: 0}, 1500, 'easeInOutExpo');
        return false;
    });

    // Cart functionality
    function updateCartCount() {
        if (AANYAAS.userLoggedIn) {
            $.ajax({
                url: AANYAAS.cartCountUrl,
                method: "GET",
                success: function(data) {
                    $('.cart-count[data-total="true"]').text(data.total_quantity || 0);
                },
                error: function(xhr, status, error) {
                    console.log('Failed to update cart count:', error);
                }
            });
        } else {
            $('.cart-count[data-total="true"]').text(0);
        }
    }

    function flashMessage(message, type) {
        var $flash = $('<div class="alert alert-' + type + ' alert-dismissible fade show" style="position: fixed; top: 20px; right: 20px; z-index: 9999;">' +
                    message +
                    '<button type="button" class="close" data-dismiss="alert">&times;</button>' +
                    '</div>');
        $('body').append($flash);
        setTimeout(function() {
            $flash.alert('close');
        }, 3000);
    }
/*
    $(document).on('click', '.add-to-cart:not(.processing), .btn-add-to-cart:not(.processing)', function(e) {
        e.preventDefault();
        var $btn = $(this).addClass('processing');
        var productId = $btn.data('product-id');
        var quantity = $btn.closest('.d-flex').find('.quantity-value') ?
                    parseInt($btn.closest('.d-flex').find('.quantity-value').val()) || 1 : 1;

        $btn.prop('disabled', true);
        $btn.html('<i class="fa fa-spinner fa-spin mr-1"></i>');

        $.ajax({
            url: AANYAAS.addToCartUrl.replace('0', productId),
            method: 'POST',
            data: {
                quantity: quantity,
                csrf_token: AANYAAS.csrfToken
            },
            success: function(response) {
                $btn.removeClass('processing');
                if (response.success) {
                    updateCartCount();
                    flashMessage(response.message, 'success');
                    setTimeout(function() {
                        window.location.href = AANYAAS.cartUrl;
                    }, 1000);
                }
            },
            error: function(xhr) {
                $btn.removeClass('processing').prop('disabled', false).html('<i class="fa fa-shopping-cart mr-1"></i> Add To Cart');
                flashMessage('Error adding item to cart', 'error');
            }
        });
    });
*/
    // Setup CSRF token for AJAX requests
    $.ajaxSetup({
        beforeSend: function(xhr, settings) {
            if (!/^(GET|HEAD|OPTIONS|TRACE)$/i.test(settings.type)) {
                xhr.setRequestHeader("X-CSRFToken", $('input[name="csrf_token"]').val());
            }
        }
    });

    // Initial cart count update
    updateCartCount();

    // Vendor carousel
    $('.vendor-carousel').owlCarousel({
        loop: true,
        margin: 29,
        nav: false,
        autoplay: true,
        smartSpeed: 1000,
        responsive: {
            0:{
                items:2
            },
            576:{
                items:3
            },
            768:{
                items:4
            },
            992:{
                items:5
            },
            1200:{
                items:6
            }
        }
    });

    // Related carousel
    $('.related-carousel').owlCarousel({
        loop: true,
        margin: 29,
        nav: false,
        autoplay: true,
        smartSpeed: 1000,
        responsive: {
            0:{
                items:1
            },
            576:{
                items:2
            },
            768:{
                items:3
            },
            992:{
                items:4
            }
        }
    });

    // Product Quantity
    $('.quantity button').on('click', function () {
        var button = $(this);
        var oldValue = button.parent().parent().find('input').val();
        if (button.hasClass('btn-plus')) {
            var newVal = parseFloat(oldValue) + 1;
        } else {
            if (oldValue > 0) {
                var newVal = parseFloat(oldValue) - 1;
            } else {
                newVal = 0;
            }
        }
        button.parent().parent().find('input').val(newVal);
    });

})(jQuery);
