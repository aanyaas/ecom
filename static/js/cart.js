// Aanyaas Enterprises - Modern Premium Sliding Cart Drawer Orchestrator
$(function () {
    // Make these globally accessible if needed
    window.openCartDrawer = openCartDrawer;
    window.closeCartDrawer = closeCartDrawer;
    window.refreshCartDrawer = refreshCartDrawer;

    function openCartDrawer() {
        $('#cartDrawerOverlay').addClass('active');
        $('#cartDrawer').addClass('active');
        $('body').css('overflow', 'hidden'); // Prevent background scroll
        refreshCartDrawer();
    }

    function closeCartDrawer() {
        $('#cartDrawerOverlay').removeClass('active');
        $('#cartDrawer').removeClass('active');
        $('#cartDrawerCouponsScreen').removeClass('active'); // Slide back coupons panel when closed
        $('#cartDrawerLoginScreen').removeClass('active'); // Slide back login panel when closed
        $('#checkoutDrawer').removeClass('active'); // Ensure checkout drawer is also closed
        $('body').css('overflow', ''); // Re-enable background scroll
    }

    // Toggle close event listeners
    $('#cartDrawerOverlay, #closeCartDrawer').on('click', function (e) {
        e.preventDefault();
        closeCartDrawer();
    });

    // Toggle collapsible pricing details under Estimated Total
    $(document).on('click', '#estimatedTotalHeader', function (e) {
        e.preventDefault();
        $('#estimatedTotalDetails').slideToggle(300);
        var $chevron = $('#estimatedTotalChevron');
        if ($chevron.hasClass('rotated')) {
            $chevron.removeClass('rotated').css('transform', 'none');
        } else {
            $chevron.addClass('rotated').css('transform', 'rotate(180deg)');
        }
    });

    // Intercept all cart navigation links globally
    $(document).on('click', 'a[href="/cart"], a[href$="/cart"], [aria-label="Shopping cart"]', function (e) {
        e.preventDefault();
        openCartDrawer();
    });

    // Global Add To Cart interceptor for all products across the site
    $(document).on('click', '.add-to-cart-btn, .add-to-cart, .btn-add-to-cart, .add-to-cart-detail', function (e) {
        // Only run if not inside best offers (handled separately)
        if ($(this).hasClass('cross-sell-add-btn')) return;

        e.preventDefault();
        e.stopPropagation();

        var $btn = $(this);
        if ($btn.hasClass('processing')) return;

        var productId = $btn.data('product-id') || $btn.attr('data-product-id');
        if (!productId) {
            productId = $btn.closest('[data-product-id]').data('product-id');
        }
        if (!productId) return;

        $btn.addClass('processing').prop('disabled', true);
        var originalHtml = $btn.html();
        $btn.html('<i class="fa fa-spinner fa-spin mr-1"></i>');

        var quantity = 1;
        var $qtyInput = $btn.closest('.d-flex').find('.quantity-value');
        if ($qtyInput.length) {
            quantity = parseInt($qtyInput.val()) || 1;
        }

        $.ajax({
            url: "/add_to_cart/" + productId,
            method: 'POST',
            data: {
                quantity: quantity,
                csrf_token: $('meta[name="csrf-token"]').attr('content')
            },
            success: function (response) {
                $btn.removeClass('processing').prop('disabled', false).html(originalHtml);
                if (response.success) {
                    // Update all cart count displays with premium pulse animation
                    $('.cart-count').text(response.cart_count).addClass('cart-badge--pulse');
                    $('.cart-count-badge').text(response.cart_count);
                    setTimeout(function () {
                        $('.cart-count').removeClass('cart-badge--pulse');
                    }, 400);

                    // Show premium toast (Success alert removed as requested)
                    // if (window.showToast) {
                    //    window.showToast('success', response.message || 'Item added to cart');
                    // }

                    // Open cart drawer dynamically
                    openCartDrawer();
                } else {
                    if (window.showToast) {
                        window.showToast('error', response.message || 'Error adding item');
                    }
                }
            },
            error: function (xhr) {
                $btn.removeClass('processing').prop('disabled', false).html(originalHtml);
                var errorMsg = 'Error adding item to cart';
                if (xhr.responseJSON && xhr.responseJSON.message) {
                    errorMsg = xhr.responseJSON.message;
                }
                if (window.showToast) {
                    window.showToast('error', errorMsg);
                }
            }
        });
    });

    // Check URL parameters for open_cart=1 on load
    var urlParams = new URLSearchParams(window.location.search);
    if (urlParams.has('open_cart') && urlParams.get('open_cart') === '1') {
        openCartDrawer();

        // Clean URL parameters without reloading
        var newUrl = window.location.protocol + "//" + window.location.host + window.location.pathname;
        window.history.replaceState({ path: newUrl }, '', newUrl);
    }
    
    if (urlParams.has('open_checkout') && urlParams.get('open_checkout') === '1') {
        openCartDrawer();
        setTimeout(function() {
            if (window.AANYAAS && window.AANYAAS.userLoggedIn) {
                window.openCheckoutDrawer();
            } else {
                $('#cartDrawerLoginScreen').addClass('active');
            }
        }, 300);

        // Clean URL parameters without reloading
        var newUrl = window.location.protocol + "//" + window.location.host + window.location.pathname;
        window.history.replaceState({ path: newUrl }, '', newUrl);
    }

    // Dynamic list event handlers: quantity adjustments
    $(document).on('click', '.qty-minus', function (e) {
        e.preventDefault();
        var productId = $(this).data('product-id');
        var $input = $(this).siblings('.cart-drawer-qty-input');
        var currentVal = parseInt($input.val()) || 1;
        if (currentVal > 1) {
            updateQty(productId, currentVal - 1);
        }
    });

    $(document).on('click', '.qty-plus', function (e) {
        e.preventDefault();
        var productId = $(this).data('product-id');
        var $input = $(this).siblings('.cart-drawer-qty-input');
        var currentVal = parseInt($input.val()) || 1;
        updateQty(productId, currentVal + 1);
    });

    // Trash button fadeout deletion
    $(document).on('click', '.remove-item-btn', function (e) {
        e.preventDefault();
        var productId = $(this).data('product-id');
        var $itemRow = $(this).closest('.cart-drawer-item');

        // Smooth slide & fadeout animation
        $itemRow.css({
            'opacity': '0',
            'transform': 'translateX(100px)',
            'max-height': '0',
            'padding-top': '0',
            'padding-bottom': '0',
            'margin-top': '0',
            'margin-bottom': '0',
            'border-bottom': 'none',
            'overflow': 'hidden',
            'transition': 'all 0.35s cubic-bezier(0.165, 0.84, 0.44, 1)'
        });

        setTimeout(function () {
            removeItem(productId);
        }, 350);
    });

    // Best Offers Cross-Sell + ADD
    $(document).on('click', '.cross-sell-add-btn', function (e) {
        e.preventDefault();
        var productId = $(this).data('product-id');
        var $btn = $(this);
        $btn.html('<i class="fa fa-spinner fa-spin"></i>').prop('disabled', true);

        $.ajax({
            url: '/add_to_cart/' + productId,
            method: 'POST',
            data: {
                quantity: 1,
                csrf_token: $('meta[name="csrf-token"]').attr('content')
            },
            success: function (response) {
                if (response.success) {
                    if (window.showToast) window.showToast('success', response.message);
                    refreshCartDrawer();
                } else {
                    if (window.showToast) window.showToast('error', response.message);
                    $btn.html('+ ADD').prop('disabled', false);
                }
            },
            error: function () {
                if (window.showToast) window.showToast('error', 'Failed to add offer item');
                $btn.html('+ ADD').prop('disabled', false);
            }
        });
    });

    // Reusable AJAX Coupon Application Helper
    function applyCoupon(code, $btn, $msgArea) {
        var originalText = $btn.html();
        $btn.html('<i class="fa fa-spinner fa-spin"></i>').prop('disabled', true);
        if ($msgArea) $msgArea.hide();

        $.ajax({
            url: '/validate-coupon',
            method: 'POST',
            data: {
                coupon_code: code,
                csrf_token: $('meta[name="csrf-token"]').attr('content')
            },
            success: function (response) {
                if (response.valid) {
                    if (window.showToast) window.showToast('success', response.message || 'Coupon applied successfully!');
                    $('#cartDrawerCouponsScreen').removeClass('active'); // Slide back to main cart view
                    refreshCartDrawer();
                } else {
                    if ($msgArea) {
                        $msgArea.text(response.message).removeClass('text-success').addClass('text-danger').show();
                    } else if (window.showToast) {
                        window.showToast('error', response.message || 'Invalid coupon code');
                    }
                    $btn.html(originalText).prop('disabled', false);
                }
            },
            error: function () {
                if (window.showToast) window.showToast('error', 'Error validating coupon');
                $btn.html(originalText).prop('disabled', false);
            }
        });
    }

    // Apply Coupon Code from Main Cart Drawer Screen
    $(document).on('click', '#applyDrawerCouponBtn', function (e) {
        e.preventDefault();
        var code = $('#drawerCouponInput').val().trim().toUpperCase();
        if (!code) {
            var $msg = $('#couponMessageArea');
            $msg.text('Please enter a coupon code').removeClass('text-success').addClass('text-danger').show();
            return;
        }
        applyCoupon(code, $(this), $('#couponMessageArea'));
    });

    // Remove Coupon Code
    $(document).on('click', '.remove-coupon-btn', function (e) {
        e.preventDefault();
        $.ajax({
            url: '/remove-applied-coupon',
            method: 'POST',
            data: {
                csrf_token: $('meta[name="csrf-token"]').attr('content')
            },
            success: function (response) {
                if (response.success) {
                    if (window.showToast) window.showToast('success', 'Coupon removed successfully!');
                    refreshCartDrawer();
                }
            }
        });
    });

    // View All Coupons Inline Screen Toggle Click
    $(document).on('click', '#viewAllCouponsBtn', function (e) {
        e.preventDefault();
        $('#cartDrawerCouponsScreen').addClass('active');
        $('#couponsScreenMessage').hide();
        $('#couponsScreenInput').val('');
    });

    // Back to Cart from Coupons Screen
    $(document).on('click', '#backToCartBtn', function (e) {
        e.preventDefault();
        $('#cartDrawerCouponsScreen').removeClass('active');
    });

    // Close entire drawer from Coupons screen
    $(document).on('click', '#closeCartDrawerFromCoupons', function (e) {
        e.preventDefault();
        closeCartDrawer();
    });

    // Apply Coupon Code via Coupons Screen Input
    $(document).on('click', '#applyCouponsScreenBtn', function (e) {
        e.preventDefault();
        var code = $('#couponsScreenInput').val().trim().toUpperCase();
        if (!code) {
            $('#couponsScreenMessage').text('Please enter a coupon code').removeClass('text-success').addClass('text-danger').show();
            return;
        }
        applyCoupon(code, $(this), $('#couponsScreenMessage'));
    });

    // Apply Coupon Code via Coupons Screen dynamic tile list
    $(document).on('click', '.coupons-screen-apply-btn', function (e) {
        e.preventDefault();
        var code = $(this).data('code');
        applyCoupon(code, $(this), $('#couponsScreenMessage'));
    });

    // AJAX Helper for Quantity Update
    function updateQty(productId, newQty) {
        $.ajax({
            url: '/update_cart_item',
            method: 'POST',
            data: {
                product_id: productId,
                quantity: newQty,
                csrf_token: $('meta[name="csrf-token"]').attr('content')
            },
            success: function (response) {
                if (response.success) {
                    refreshCartDrawer();
                } else {
                    if (window.showToast) window.showToast('error', response.error || 'Failed to update quantity');
                    refreshCartDrawer();
                }
            },
            error: function (xhr) {
                var err = 'Error updating quantity';
                if (xhr.responseJSON && xhr.responseJSON.error) {
                    err = xhr.responseJSON.error;
                }
                if (window.showToast) window.showToast('error', err);
                refreshCartDrawer();
            }
        });
    }

    // AJAX Helper for Deletion
    function removeItem(productId) {
        $.ajax({
            url: '/remove-from-cart/' + productId,
            method: 'POST',
            data: {
                csrf_token: $('meta[name="csrf-token"]').attr('content')
            },
            success: function (response) {
                if (response.success) {
                    if (window.showToast) window.showToast('success', 'Item removed from cart');
                    refreshCartDrawer();
                } else {
                    if (window.showToast) window.showToast('error', 'Error removing item');
                    refreshCartDrawer();
                }
            },
            error: function () {
                if (window.showToast) window.showToast('error', 'Error removing item');
                refreshCartDrawer();
            }
        });
    }

    // Comprehensive Drawer Dynamic Render Function
    function refreshCartDrawer() {
        var $itemsContainer = $('#cartDrawerItems');
        var $footerContent = $('#cartDrawerFooterContent');
        var $shippingContainer = $('#freeShippingContainer');

        // Show premium shimmering/loading placeholder state
        $itemsContainer.html(`
            <div class="text-center py-5">
                <div class="spinner-border" role="status" style="color: var(--primary-color); width: 2.5rem; height: 2.5rem; border-width: 3px;">
                    <span class="sr-only">Loading Cart...</span>
                </div>
            </div>
        `);

        $.ajax({
            url: '/api/cart-details',
            method: 'GET',
            cache: false,
            success: function (data) {
                if (!data.success) {
                    $itemsContainer.html('<div class="alert alert-danger m-3">Failed to load cart details.</div>');
                    return;
                }

                // Sync count markers with premium pulse animation on value change
                var currentCount = parseInt($('.cart-count').first().text()) || 0;
                if (currentCount !== data.total_quantity) {
                    $('.cart-count').text(data.total_quantity).addClass('cart-badge--pulse');
                    setTimeout(function () {
                        $('.cart-count').removeClass('cart-badge--pulse');
                    }, 400);
                } else {
                    $('.cart-count').text(data.total_quantity);
                }
                $('.cart-count-badge').text(data.total_quantity);

                // 1. Handle EMPTY CART STATE
                if (!data.cart_items || data.cart_items.length === 0) {
                    $shippingContainer.hide();
                    $itemsContainer.html(`
                        <div class="text-center py-5 empty-cart-drawer animate__animated animate__fadeIn">
                            <div class="mb-4">
                                <i class="fas fa-shopping-bag text-muted" style="font-size: 4rem; opacity: 0.25;"></i>
                            </div>
                            <h5 class="font-weight-bold mb-2" style="font-family: 'Playfair Display', serif; color: #2b2725; font-size: 1.15rem;">Your Cart is Empty</h5>
                            <p class="text-muted small px-4 mb-4">Add some beautiful handmade jewellery and crafts to your bag to make it happy!</p>
                            <a href="/shop" class="btn btn-dark px-4 py-2 font-weight-bold" style="border-radius: 30px; font-size: 0.8rem; letter-spacing: 1px; background: var(--dark-color);">BROWSE SHOP</a>
                        </div>
                    `);
                    $footerContent.html(`
                        <div class="text-center py-2">
                            <a href="/shop" class="btn btn-block btn-outline-dark py-3 font-weight-bold text-uppercase" style="border-radius: 50px; font-size: 0.8rem; letter-spacing: 1px;">Continue Shopping</a>
                        </div>
                    `);
                    return;
                }

                // Dynamically Render Coupons Screen dynamic tile list (moved up for milestones)
                var coupons = [
                    { code: 'F10', discount: 10, minOrder: 1500, description: 'Get 10% off on orders above ₹1,500.00' },
                    { code: 'F15', discount: 15, minOrder: 2500, description: 'Get 15% off on orders above ₹2,500.00' },
                    { code: 'F20', discount: 20, minOrder: 5000, description: 'Get 20% off on orders above ₹5,000.00' },
                    { code: 'F25', discount: 25, minOrder: 10000, description: 'Get 25% off on orders above ₹10,000.00' }
                ];

                // 2. Render MILESTONE PROGRESS BAR
                $shippingContainer.show();
                var subtotal = data.subtotal;
                var nextMilestone = null;
                var currentMilestone = null;
                for (var i = 0; i < coupons.length; i++) {
                    if (subtotal < coupons[i].minOrder) {
                        nextMilestone = coupons[i];
                        if (i > 0) currentMilestone = coupons[i-1];
                        break;
                    }
                }
                
                if (!nextMilestone && subtotal >= coupons[coupons.length - 1].minOrder) {
                    currentMilestone = coupons[coupons.length - 1];
                }

                if (!nextMilestone && currentMilestone) {
                    // Reached max milestone
                    $shippingContainer.html(`
                        <div class="free-shipping-bar-wrapper text-center py-2" style="position: relative;">
                            <span class="small font-weight-bold" style="font-size: 0.85rem; color: #a64d79;">
                                🎉 You have successfully reached the milestone!
                            </span>
                            <div class="progress mt-3" style="height: 6px; border-radius: 10px; background: #e8dee8;">
                                <div class="progress-bar progress-bar-striped progress-bar-animated" role="progressbar" style="width: 100%; background: #a64d79;" aria-valuenow="100" aria-valuemin="0" aria-valuemax="100"></div>
                            </div>
                        </div>
                    `);
                    
                    if (!window.milestoneReachedConfettiShown) {
                        window.milestoneReachedConfettiShown = true;
                        if (typeof confetti === 'function') confetti({ particleCount: 100, spread: 70, origin: { y: 0.3 }, zIndex: 10000 });
                    }
                } else if (nextMilestone) {
                    var remaining = nextMilestone.minOrder - subtotal;
                    var base = currentMilestone ? currentMilestone.minOrder : 0;
                    var progressRange = nextMilestone.minOrder - base;
                    var currentProgress = subtotal - base;
                    var percentage = Math.min((currentProgress / progressRange) * 100, 100);
                    
                    var overallPercentage = Math.min((subtotal / coupons[coupons.length-1].minOrder) * 100, 100);
                    var dotPos = (nextMilestone.minOrder / coupons[coupons.length-1].minOrder)*100;
                    
                    $shippingContainer.html(`
                        <div class="free-shipping-bar-wrapper py-2" style="position: relative;">
                            <div class="d-flex justify-content-between mb-2">
                                <span class="small font-weight-bold text-dark" style="font-size: 0.78rem; font-family: 'Poppins', sans-serif;">
                                    Add <strong>₹${remaining.toFixed(2)}</strong> more for <strong>${nextMilestone.discount}% OFF</strong>!
                                </span>
                            </div>
                            <div class="progress" style="height: 6px; border-radius: 10px; background: #e8dee8; overflow: visible; position: relative;">
                                <div class="progress-bar progress-bar-striped progress-bar-animated" role="progressbar" style="width: ${overallPercentage}%; background-color: var(--primary-color); border-radius: 10px;" aria-valuenow="${overallPercentage}" aria-valuemin="0" aria-valuemax="100"></div>
                                
                                <div style="position: absolute; left: ${dotPos}%; top: 50%; transform: translate(-50%, -50%); width: 14px; height: 14px; background: white; border: 3px solid #e8dee8; border-radius: 50%; z-index: 2;"></div>
                                <div style="position: absolute; left: ${dotPos}%; top: 15px; transform: translateX(-50%); font-size: 0.65rem; color: #888; font-weight: bold; white-space: nowrap;">Get ${nextMilestone.discount}% off!</div>
                            </div>
                            <div style="height: 15px;"></div>
                        </div>
                    `);
                    window.milestoneReachedConfettiShown = false;
                } else {
                    $shippingContainer.html('');
                }

                // 3. Build List of Items
                var itemsHtml = '<div class="cart-items-list mb-3">';
                data.cart_items.forEach(function (item) {
                    var itemMrp = item.mrp || item.price;
                    var originalPriceHtml = '';
                    if (itemMrp > item.price) {
                        originalPriceHtml = `<span class="text-muted text-decoration-line-through mr-2" style="font-size: 0.8rem; text-decoration: line-through;">₹${itemMrp.toFixed(2)}</span>`;
                    }

                    itemsHtml += `
                        <div class="cart-drawer-item" data-product-id="${item.id}">
                            <a href="/detail/${item.id}">
                                <img src="/static/img/thumbs/${item.image || 'default.jpg'}" alt="${item.name}" class="cart-drawer-item-img mr-3">
                            </a>
                            <div class="flex-grow-1" style="min-width: 0;">
                                <a href="/detail/${item.id}" class="cart-drawer-item-title d-block font-weight-bold mb-1" style="font-size: 0.85rem; line-height: 1.3;">${item.name}</a>
                                <div class="d-flex align-items-center mb-2">
                                    ${originalPriceHtml}
                                    <span class="font-weight-bold" style="color: var(--primary-color); font-size: 0.88rem;">₹${item.price.toFixed(2)}</span>
                                </div>
                                
                                <div class="d-flex align-items-center justify-content-between">
                                    <div class="cart-drawer-qty-pill">
                                        <button class="cart-drawer-qty-btn qty-minus" data-product-id="${item.id}" ${item.quantity <= 1 ? 'disabled' : ''}>
                                            <i class="fas fa-minus"></i>
                                        </button>
                                        <input type="text" class="cart-drawer-qty-input" value="${item.quantity}" readonly>
                                        <button class="cart-drawer-qty-btn qty-plus" data-product-id="${item.id}" ${item.quantity >= item.stock_quantity ? 'disabled' : ''}>
                                            <i class="fas fa-plus"></i>
                                        </button>
                                    </div>
                                    <button class="cart-drawer-trash remove-item-btn" data-product-id="${item.id}" aria-label="Remove item">
                                        <i class="far fa-trash-alt"></i>
                                    </button>
                                </div>
                            </div>
                        </div>
                    `;
                });
                itemsHtml += '</div>';

                // 4. Append Cross-Sells (Best Offers)
                if (data.best_offers && data.best_offers.length > 0) {
                    itemsHtml += `
                        <div class="best-offers-section mt-4 mb-2 pt-3 border-top">
                            <h6 class="font-weight-bold text-uppercase small text-muted mb-3" style="letter-spacing: 1.5px; font-size: 0.7rem;">Best Offers for You</h6>
                            <div class="best-offers-list">
                    `;
                    data.best_offers.forEach(function (offer) {
                        var offerMrp = offer.mrp || offer.price;
                        var offerMrpHtml = '';
                        if (offerMrp > offer.price) {
                            offerMrpHtml = `<span class="text-muted text-decoration-line-through mr-1" style="font-size: 0.72rem; text-decoration: line-through;">₹${offerMrp.toFixed(2)}</span>`;
                        }
                        itemsHtml += `
                            <div class="best-offer-item mb-2 d-flex align-items-center justify-content-between">
                                <div class="d-flex align-items-center" style="min-width: 0; flex-grow: 1;">
                                    <img src="/static/img/thumbs/${offer.image || 'default.jpg'}" alt="${offer.name}" class="best-offer-img mr-2">
                                    <div style="min-width: 0; flex-grow: 1; padding-right: 10px;">
                                        <span class="d-block font-weight-bold text-dark text-truncate" style="font-size: 0.78rem; font-family: 'Poppins', sans-serif;">${offer.name}</span>
                                        <div class="d-flex align-items-center">
                                            ${offerMrpHtml}
                                            <span class="font-weight-bold" style="color: var(--primary-color); font-size: 0.78rem;">₹${offer.price.toFixed(2)}</span>
                                        </div>
                                    </div>
                                </div>
                                <button class="best-offer-add-btn cross-sell-add-btn" data-product-id="${offer.id}">+ ADD</button>
                            </div>
                        `;
                    });
                    itemsHtml += `
                            </div>
                        </div>
                    `;
                }

                $itemsContainer.html(itemsHtml);

                // Populate Cart value in dynamic coupons screen
                $('#couponsScreenCartValue').text('₹' + data.subtotal.toFixed(2));

                // (Coupons array definition moved up)

                var couponsScreenHtml = '';
                coupons.forEach(function (coupon) {
                    if (data.subtotal >= coupon.minOrder) {
                        var savingsVal = data.subtotal * (coupon.discount / 100);
                        couponsScreenHtml += `
                            <div class="coupon-tile-premium d-flex justify-content-between align-items-center" data-code="${coupon.code}">
                                <div style="flex-grow: 1; padding-right: 15px;">
                                    <span class="coupon-badge mb-2">${coupon.discount}% OFF</span>
                                    <h6 class="font-weight-bold m-0 text-dark" style="font-size: 0.9rem; font-family: 'Poppins', sans-serif;">Use Code: ${coupon.code}</h6>
                                    <p class="text-muted small m-0 mt-1" style="font-size: 0.75rem; line-height: 1.3;">${coupon.description}</p>
                                    <span class="text-success small font-weight-bold mt-2 d-block" style="font-size: 0.75rem;"><i class="fas fa-check-circle mr-1"></i> Save ₹${savingsVal.toFixed(2)} on this order!</span>
                                </div>
                                <button class="coupon-tile-apply-btn btn btn-sm font-weight-bold coupons-screen-apply-btn" data-code="${coupon.code}" style="background: var(--primary-color); color: white; border-radius: 20px; padding: 4px 12px; font-size: 0.75rem; flex-shrink: 0;">Apply</button>
                            </div>
                        `;
                    } else {
                        var remainingVal = coupon.minOrder - data.subtotal;
                        couponsScreenHtml += `
                            <div class="coupon-tile-premium disabled d-flex justify-content-between align-items-center" style="background: #faf8fa; border-color: #dcd7dc;">
                                <div style="flex-grow: 1; padding-right: 15px;">
                                    <span class="coupon-badge mb-2" style="background: #f0edf0; color: #8c888c;">${coupon.discount}% OFF</span>
                                    <h6 class="font-weight-bold m-0 text-muted" style="font-size: 0.9rem; font-family: 'Poppins', sans-serif;">Use Code: ${coupon.code}</h6>
                                    <p class="text-muted small m-0 mt-1" style="font-size: 0.75rem; line-height: 1.3;">${coupon.description}</p>
                                    <span class="text-danger small font-weight-bold mt-2 d-block" style="font-size: 0.75rem;"><i class="fas fa-exclamation-circle mr-1"></i> Add ₹${remainingVal.toFixed(2)} more to avail this offer</span>
                                </div>
                                <button class="coupon-tile-apply-btn btn btn-sm font-weight-bold disabled" disabled style="background: #e8e5e8; color: #8c888c !important; flex-shrink: 0;">Apply</button>
                            </div>
                        `;
                    }
                });
                $('#couponsScreenListContainer').html(couponsScreenHtml);

                // 5. Render Checkout Pricing Footer Content
                var couponHtml = '';
                if (data.coupon_code) {
                    couponHtml = `
                        <div class="coupon-section mb-2">
                            <label class="small font-weight-bold text-uppercase text-muted d-block mb-2" style="letter-spacing: 1px; font-size: 0.7rem;">Applied Promo Code</label>
                            <div class="d-flex align-items-center justify-content-between p-2 rounded" style="background: rgba(166, 77, 121, 0.05); border: 1.2px dashed var(--primary-color);">
                                <div class="d-flex align-items-center">
                                    <i class="fas fa-tag mr-2" style="color: var(--primary-color); font-size: 0.8rem;"></i>
                                    <span class="font-weight-bold text-uppercase" style="color: var(--primary-color); font-size: 0.8rem;">${data.coupon_code}</span>
                                    <span class="badge ml-2 small" style="background: rgba(40, 167, 69, 0.1); color: #28a745; font-size: 0.65rem; border: 1px solid rgba(40,167,69,0.2);">ACTIVE</span>
                                </div>
                                <button class="btn btn-sm text-muted p-0 font-weight-bold remove-coupon-btn" style="background:none; border:none; outline:none; font-size: 1.2rem; line-height: 1;">&times;</button>
                            </div>
                        </div>
                    `;
                } else {
                    couponHtml = `
                        <div class="coupon-section mb-2 border rounded p-2 d-flex justify-content-between align-items-center" style="background: #fff; cursor: pointer; border-color: #e8dee8 !important;" id="viewAllCouponsBtn">
                            <div class="d-flex align-items-center">
                                <i class="fas fa-percentage mr-2" style="color: #28a745; font-size: 0.9rem;"></i>
                                <span class="font-weight-bold text-dark" style="font-size: 0.85rem;">Apply Promo Code</span>
                            </div>
                            <button class="btn btn-sm" type="button" style="background: #eaf5ea; color: #475a34; font-weight: 600; padding: 4px 12px; border-radius: 5px;">Apply</button>
                        </div>
                    `;
                }

                // Calculate Total MRP, Discount on MRP and Savings
                var totalMrp = 0;
                data.cart_items.forEach(function (item) {
                    var itemMrp = item.mrp || item.price;
                    totalMrp += itemMrp * item.quantity;
                });
                var mrpDiscount = totalMrp - data.subtotal;
                var savingsPercentage = totalMrp > 0 ? Math.round((data.total_savings / totalMrp) * 100) : 0;

                var savingsTextHtml = data.total_savings > 0 ? `
                    <div class="small font-weight-bold text-success text-right" style="font-size: 0.72rem; margin-top: -2px;">You saved ₹${data.total_savings.toFixed(2)}!</div>
                ` : '';

                var savingsSummaryHtml = data.total_savings > 0 ? `
                    <div class="p-2 rounded text-center small mt-2" style="background: #eef9f1; color: #1e7e34; font-weight: 600; font-size: 0.78rem; border: 1px solid rgba(30,126,52,0.1);">
                        You Saved ₹${data.total_savings.toFixed(2)} (${savingsPercentage}%) so far!
                    </div>
                ` : '';

                var freeShippingBannerHtml = '';
                if (data.subtotal >= data.free_shipping_threshold) {
                    freeShippingBannerHtml = `
                    <div class="mb-2 p-2 text-center font-weight-bold text-white rounded" style="background: #626c48; font-size: 0.75rem; letter-spacing: 0.5px;">
                        Free Shipping Available
                    </div>`;
                }

                var footerHtml = `
                    ${couponHtml}
                    ${freeShippingBannerHtml}
                    <div class="price-summary mb-2 p-2 rounded" style="background: #faf8fa; border: 1px solid #f2eef2;">
                        <!-- Collapsible Estimated Total Header -->
                        <div class="d-flex justify-content-between align-items-center py-1" id="estimatedTotalHeader" style="cursor: pointer; user-select: none;">
                            <span class="font-weight-bold text-dark d-flex align-items-center" style="font-size: 0.85rem; font-family: 'Poppins', sans-serif;">
                                <i class="far fa-credit-card mr-2 text-muted" style="font-size: 0.85rem;"></i> Estimated total
                            </span>
                            <div class="text-right">
                                <span class="font-weight-bold text-dark d-inline-flex align-items-center" style="font-size: 1.05rem;">
                                    ${data.total_savings > 0 ? `<span class="text-muted text-decoration-line-through mr-1" style="font-size: 0.8rem; font-weight: normal;">₹${(data.total + data.total_savings).toFixed(2)}</span>` : ''}
                                    ₹${data.total.toFixed(2)} 
                                    <i class="fas fa-chevron-down ml-2" id="estimatedTotalChevron" style="font-size: 0.78rem; transition: transform 0.3s; color: #666;"></i>
                                </span>
                                ${savingsTextHtml}
                            </div>
                        </div>
                        
                        <!-- Collapsible Details Block -->
                        <div id="estimatedTotalDetails" style="display: none; border-top: 1px dashed #e8dee8; margin-top: 8px; padding-top: 8px;">
                            <div class="d-flex justify-content-between mb-1.5" style="font-size: 0.78rem; color: #555;">
                                <span>Total MRP</span>
                                <span class="font-weight-bold text-dark">₹${totalMrp.toFixed(2)}</span>
                            </div>
                            <div class="d-flex justify-content-between mb-1.5" style="font-size: 0.78rem; color: #555;">
                                <span>Discount on MRP</span>
                                <span class="font-weight-bold text-success">-₹${mrpDiscount.toFixed(2)}</span>
                            </div>
                            <div class="d-flex justify-content-between mb-1.5" style="font-size: 0.78rem; color: #555;">
                                <span>Coupon discount</span>
                                <span class="font-weight-bold text-success">-₹${data.discount_amount.toFixed(2)}</span>
                            </div>
                            <div class="d-flex justify-content-between mb-1.5" style="font-size: 0.78rem; color: #555;">
                                <span>Delivery fee</span>
                                <span class="font-weight-bold ${data.shipping_charge > 0 ? 'text-dark' : 'text-success'}">${data.shipping_charge > 0 ? '₹' + data.shipping_charge.toFixed(2) : 'FREE'}</span>
                            </div>
                            <hr class="my-1.5" style="border-color: #e8dee8; border-style: dashed; margin-top: 6px; margin-bottom: 6px;">
                            <div class="d-flex justify-content-between mb-1 font-weight-bold" style="font-size: 0.82rem; color: #1a1a1a;">
                                <span>Grand total</span>
                                <span style="color: var(--primary-color, #a64d79);">₹${data.total.toFixed(2)}</span>
                            </div>
                            ${savingsSummaryHtml}
                        </div>
                    </div>
                    
                    <a href="/checkout" class="btn btn-block font-weight-bold text-uppercase d-flex justify-content-between align-items-center shadow-sm" id="checkoutBtn" style="border-radius: 50px; background: linear-gradient(135deg, var(--primary-color, #a64d79), var(--secondary-color, #ba6286)) !important; color: white !important; letter-spacing: 1px; font-size: 0.9rem; border: none; text-decoration: none; padding: 10px 16px 10px 24px; height: 50px; line-height: 1; transition: all 0.3s ease;">
                        <span style="font-size: 0.9rem; font-weight: 700; text-transform: uppercase;"><i class="fas fa-lock mr-2" style="font-size: 0.8rem; opacity: 0.8;"></i>Checkout <i class="fas fa-arrow-right ml-1" style="font-size: 0.8rem;"></i></span>
                        <div class="d-flex align-items-center bg-white rounded-pill px-3 shadow-sm" style="gap: 8px; height: 32px; border: 1px solid rgba(0,0,0,0.05);">
                            <img src="https://img.icons8.com/color/48/000000/paytm.png" alt="Paytm" style="height: 14px; object-fit: contain;">
                            <img src="https://img.icons8.com/color/48/000000/google-pay.png" alt="Google Pay" style="height: 14px; object-fit: contain;">
                            <img src="https://img.icons8.com/color/48/000000/visa.png" alt="Visa" style="height: 12px; object-fit: contain;">
                            <img src="https://img.icons8.com/color/48/000000/mastercard.png" alt="Mastercard" style="height: 14px; object-fit: contain;">
                        </div>
                    </a>             
                `;

                $footerContent.html(footerHtml);
            },
            error: function (xhr) {
                $itemsContainer.html('<div class="alert alert-danger m-3">Error loading cart. Please try again.</div>');
            }
        });
    }
});
    window.openCheckoutDrawer = function() {
        $('#cartDrawer').removeClass('active');
        $('#cartDrawerLoginScreen').removeClass('active');
        $('#checkoutDrawer').html('<div class="p-5 text-center"><i class="fas fa-spinner fa-spin fa-2x text-primary"></i></div>').addClass('active');
        
        $.get('/checkout-drawer', function(html) {
            if(html === "Your cart is empty.") {
                alert(html);
                $('#checkoutDrawer').removeClass('active');
                return;
            }
            $('#checkoutDrawer').html(html);
        }).fail(function() {
            alert('Error loading checkout. Please try again.');
            $('#checkoutDrawer').removeClass('active');
        });
    };

    // Checkout button click logic
    $(document).on('click', '#checkoutBtn', function(e) {
        e.preventDefault();
        if (!window.AANYAAS || !window.AANYAAS.userLoggedIn) {
            $('#cartDrawerLoginScreen').addClass('active');
        } else {
            window.openCheckoutDrawer();
        }
    });
    
    // Close cart login screen
    $(document).on('click', '#closeCartLoginScreen', function(e) {
        e.preventDefault();
        $('#cartDrawerLoginScreen').removeClass('active');
    });

    // Toggle cart login mode
    $(document).on('click', '.toggle-cart-login-mode', function() {
        $('#cartPasswordSection, #cartOtpSection').toggleClass('d-none');
        const isOtpMode = !$('#cartOtpSection').hasClass('d-none');
        
        $('#cartLoginPassword').prop('required', !isOtpMode);
        $('#cartLoginOtp').prop('required', isOtpMode);
        
        if (isOtpMode) {
            $('#sendCartOtpWrapper').removeClass('d-none');
            $('#cartLoginUsername').css('border-radius', '10px 0 0 10px');
        } else {
            $('#sendCartOtpWrapper').addClass('d-none');
            $('#cartLoginUsername').css('border-radius', '10px');
        }
    });

    // Send Cart OTP Logic
    let cartOtpCountdown;
    function startCartOtpTimer() {
        let seconds = 60;
        $('#sendCartOtpBtn').prop('disabled', true).text('Resend in ' + seconds + 's');
        
        if (cartOtpCountdown) clearInterval(cartOtpCountdown);
        cartOtpCountdown = setInterval(function() {
            seconds--;
            $('#sendCartOtpBtn').text('Resend in ' + seconds + 's');
            if (seconds <= 0) {
                clearInterval(cartOtpCountdown);
                $('#sendCartOtpBtn').prop('disabled', false).text('Resend OTP');
            }
        }, 1000);
    }

    $(document).on('click', '#sendCartOtpBtn', function() {
        const identifier = $('#cartLoginUsername').val();
        if (!identifier) {
            $('#cartDrawerLoginAlert').removeClass('d-none alert-success').addClass('alert-danger').text('Please enter your email or mobile number.');
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
                    $('#cartDrawerLoginAlert').removeClass('d-none alert-danger').addClass('alert-success').text(res.message || 'OTP sent successfully!');
                    startCartOtpTimer();
                } else {
                    $('#cartDrawerLoginAlert').removeClass('d-none alert-success').addClass('alert-danger').text(res.message || 'Failed to send OTP.');
                    $btn.prop('disabled', false).text(originalText);
                }
            },
            error: function(xhr) {
                let msg = 'Failed to send OTP. Please try again.';
                if (xhr.responseJSON && xhr.responseJSON.message) msg = xhr.responseJSON.message;
                $('#cartDrawerLoginAlert').removeClass('d-none alert-success').addClass('alert-danger').text(msg);
                $btn.prop('disabled', false).text(originalText);
            }
        });
    });

    // Verify Cart OTP Logic
    $(document).on('click', '#cartOtpVerifyBtn', function() {
        const identifier = $('#cartLoginUsername').val();
        const otp = $('#cartLoginOtp').val();
        if (!otp) {
            $('#cartDrawerLoginAlert').removeClass('d-none alert-success').addClass('alert-danger').text('Please enter the 6-digit OTP.');
            return;
        }

        const isEmail = identifier.includes('@');
        const endpoint = isEmail ? '/verify-login-otp' : '/verify-whatsapp-otp';
        const data = isEmail ? { email: identifier, otp: otp } : { mobile: identifier, otp: otp };
        data.next = '/checkout';
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
                    window.AANYAAS.userLoggedIn = true;
                    $('#cartDrawerLoginAlert').removeClass('d-none alert-danger').addClass('alert-success').text('Login successful!');
                    setTimeout(function() { 
                        if (res.next && res.next !== '/checkout') {
                            window.location.href = res.next;
                        } else {
                            window.openCheckoutDrawer();
                        }
                    }, 800);
                } else {
                    $('#cartDrawerLoginAlert').removeClass('d-none alert-success').addClass('alert-danger').text(res.message || 'Invalid OTP.');
                    $btn.prop('disabled', false).html(originalHtml);
                }
            },
            error: function(xhr) {
                let msg = 'Verification failed. Please try again.';
                if (xhr.responseJSON && xhr.responseJSON.message) msg = xhr.responseJSON.message;
                $('#cartDrawerLoginAlert').removeClass('d-none alert-success').addClass('alert-danger').text(msg);
                $btn.prop('disabled', false).html(originalHtml);
            }
        });
    });

    // Handle cart drawer login form submission (Password login)
    $(document).on('submit', '#cartDrawerLoginForm', function(e) {
        e.preventDefault();
        
        // If OTP section is visible, clicking enter should verify OTP instead of password login
        if (!$('#cartOtpSection').hasClass('d-none')) {
            $('#cartOtpVerifyBtn').click();
            return;
        }

        var $form = $(this);
        var $btn = $('#cartLoginSubmitBtn');
        var $spinner = $('#cartLoginBtnSpinner');
        var $text = $('#cartLoginBtnText');
        var $alert = $('#cartDrawerLoginAlert');
        
        var username = $('#cartLoginUsername').val();
        var password = $('#cartLoginPassword').val();
        
        $btn.prop('disabled', true);
        $spinner.removeClass('d-none');
        $text.hide();
        
        $.ajax({
            url: '/login',
            method: 'POST',
            data: {
                username: username,
                password: password,
                csrf_token: $('input[name="csrf_token"]').val()
            },
            success: function(response) {
                if (response.success) {
                    window.AANYAAS.userLoggedIn = true;
                    $alert.removeClass('d-none alert-danger').addClass('alert-success').text('Login successful!');
                    setTimeout(function() {
                        window.openCheckoutDrawer();
                    }, 800);
                } else {
                    $alert.removeClass('d-none alert-success').addClass('alert-danger').text(response.message || 'Invalid credentials.');
                    $btn.prop('disabled', false);
                    $spinner.addClass('d-none');
                    $text.show();
                }
            },
            error: function() {
                $alert.removeClass('d-none alert-success').addClass('alert-danger').text('Error communicating with server.');
                $btn.prop('disabled', false);
                $spinner.addClass('d-none');
                $text.show();
            }
        });
    });