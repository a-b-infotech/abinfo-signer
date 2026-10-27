<?php
// activate_web.php
// A B INFOTECH - Unified Digital Signer & PDF Power Suite License API
ini_set('display_errors', 0);
error_reporting(0);

// Set Timezone to India to ensure EXACT time matching for expiration
date_default_timezone_set('Asia/Kolkata');

require_once 'db.php'; 

// ====================================================================
// 1. HANDLE BROWSER SYNC (Opens in browser, redirects to Python)
// ====================================================================
if (isset($_GET['action']) && $_GET['action'] === 'browser_sync') {
    $license_key = isset($_REQUEST['key']) ? trim($_REQUEST['key']) : '';
    
    if (empty($license_key)) {
        header("Location: http://127.0.0.1:5000/api/sync_callback?status=invalid");
        exit;
    }

    try {
        $stmt = $pdo->prepare("SELECT id, status, expiredate, license_tier, activated_at FROM license_keys WHERE license_key = :key LIMIT 1");
        $stmt->bindParam(':key', $license_key);
        $stmt->execute();

        if ($stmt->rowCount() > 0) {
            $row = $stmt->fetch(PDO::FETCH_ASSOC);
            $tier = (!empty($row['license_tier']) && strtolower($row['license_tier']) === 'basic') ? 'basic' : 'pro';
            
            // If key status is inactive, activate key and set starting date and time as current system time
            if (strtolower($row['status']) === 'inactive') {
                if (empty($row['expiredate']) || $row['expiredate'] === '0000-00-00 00:00:00') {
                    $upd = $pdo->prepare("UPDATE license_keys SET status = 'active', activated_at = NOW(), expiredate = DATE_ADD(NOW(), INTERVAL 1 YEAR) WHERE id = :id");
                    $upd->execute([':id' => $row['id']]);
                    $row['expiredate'] = date('Y-m-d H:i:s', strtotime('+1 year'));
                } else {
                    $upd = $pdo->prepare("UPDATE license_keys SET status = 'active', activated_at = NOW() WHERE id = :id");
                    $upd->execute([':id' => $row['id']]);
                }
                $row['status'] = 'active';
            }

            if ($row['status'] !== 'active') {
                header("Location: http://127.0.0.1:5000/api/sync_callback?status=inactive");
            } 
            elseif (!empty($row['expiredate']) && $row['expiredate'] !== 'never' && strtotime($row['expiredate']) < time()) {
                header("Location: http://127.0.0.1:5000/api/sync_callback?status=expired");
            } else {
                header("Location: http://127.0.0.1:5000/api/sync_callback?status=active&tier=" . urlencode($tier));
            }
        } else {
            header("Location: http://127.0.0.1:5000/api/sync_callback?status=invalid");
        }
    } catch (Exception $e) {
        header("Location: http://127.0.0.1:5000/api/sync_callback?status=error");
    }
    exit;
}

// ====================================================================
// 2. HANDLE BACKGROUND API VERIFICATION
// ====================================================================
if (isset($_GET['action']) && $_GET['action'] === 'verify_license') {
    header('Content-Type: application/json');
    $key = $_REQUEST['key'] ?? '';

    // Developer Bypass
    if ($key === 'ABI-MASTER-PASS') {
        echo json_encode([
            'status' => 'active',
            'tier' => 'pro',
            'expiredate' => 'never'
        ]);
        exit;
    }

    try {
        $stmt = $pdo->prepare("SELECT status, expiredate, license_tier FROM license_keys WHERE license_key = :key LIMIT 1");
        $stmt->bindParam(':key', $key);
        $stmt->execute();

        if ($stmt->rowCount() > 0) {
            $row = $stmt->fetch(PDO::FETCH_ASSOC);
            $tier = (!empty($row['license_tier']) && strtolower($row['license_tier']) === 'basic') ? 'basic' : 'pro';
            
            if ($row['status'] !== 'active') {
                echo json_encode(['status' => 'inactive']);
            } 
            elseif (!empty($row['expiredate']) && $row['expiredate'] !== 'never' && strtotime($row['expiredate']) < time()) {
                echo json_encode(['status' => 'expired']);
            } else {
                echo json_encode([
                    'status' => 'active',
                    'tier' => $tier,
                    'expiredate' => !empty($row['expiredate']) ? $row['expiredate'] : 'never'
                ]);
            }
        } else {
            echo json_encode(['status' => 'invalid', 'message' => 'License key not found!']);
        }
    } catch (Exception $e) {
        echo json_encode(['status' => 'error', 'message' => 'Database Error']);
    }
    exit;
}

// ====================================================================
// 3. HANDLE BROWSER ACTIVATION (When User enters key in Desktop App)
// ====================================================================
$license_key = isset($_REQUEST['key']) ? trim($_REQUEST['key']) : '';

if (empty($license_key)) {
    header("Location: http://127.0.0.1:5000/api/activate_callback?status=invalid&message=" . urlencode("No license key provided. Please contact +91 7016797720 or sales@a-b-infotech.in"));
    exit;
}

try {
    $query = "SELECT id, status, expiredate, license_tier, activated_at FROM license_keys WHERE license_key = :key LIMIT 1";
    $stmt = $pdo->prepare($query);
    $stmt->bindParam(':key', $license_key);
    $stmt->execute();

    if ($stmt->rowCount() > 0) {
        $row = $stmt->fetch(PDO::FETCH_ASSOC);
        
        // If key status in database is inactive: activate key and set starting date and time as current system date and time
        if (strtolower($row['status']) === 'inactive') {
            if (empty($row['expiredate']) || $row['expiredate'] === '0000-00-00 00:00:00') {
                $upd = $pdo->prepare("UPDATE license_keys SET status = 'active', activated_at = NOW(), expiredate = DATE_ADD(NOW(), INTERVAL 1 YEAR) WHERE id = :id");
                $upd->execute([':id' => $row['id']]);
                $row['expiredate'] = date('Y-m-d H:i:s', strtotime('+1 year'));
            } else {
                $upd = $pdo->prepare("UPDATE license_keys SET status = 'active', activated_at = NOW() WHERE id = :id");
                $upd->execute([':id' => $row['id']]);
            }
            $row['status'] = 'active';
            $row['activated_at'] = date('Y-m-d H:i:s');
        }

        // Block if suspended, revoked or blocked
        if ($row['status'] !== 'active') {
            header("Location: http://127.0.0.1:5000/api/activate_callback?status=invalid&message=" . urlencode("This license key is " . $row['status'] . ". Please contact +91 7016797720 (sales@a-b-infotech.in) to activate."));
            exit;
        }

        // Block if expired
        if (!empty($row['expiredate']) && $row['expiredate'] !== 'never' && strtotime($row['expiredate']) < time()) {
            header("Location: http://127.0.0.1:5000/api/activate_callback?status=invalid&message=" . urlencode("This license key has expired. Please contact +91 7016797720 (sales@a-b-infotech.in) to renew."));
            exit;
        }

        // Update activated_at if first time activated and empty
        if (empty($row['activated_at']) || $row['activated_at'] === '0000-00-00 00:00:00') {
            $upd = $pdo->prepare("UPDATE license_keys SET activated_at = NOW() WHERE id = :id");
            $upd->execute([':id' => $row['id']]);
        }

        $exp_date = !empty($row['expiredate']) ? $row['expiredate'] : 'never';
        $tier = (!empty($row['license_tier']) && strtolower($row['license_tier']) === 'basic') ? 'basic' : 'pro';

        header("Location: http://127.0.0.1:5000/api/activate_callback?status=valid&key=" . urlencode($license_key) . "&expiredate=" . urlencode($exp_date) . "&tier=" . urlencode($tier));
        exit;
    } else {
        // If license key not found: send error
        header("Location: http://127.0.0.1:5000/api/activate_callback?status=invalid&message=" . urlencode("License key not found! Please check your license key or contact +91 7016797720 / sales@a-b-infotech.in."));
        exit;
    }
} catch (Exception $e) {
    header("Location: http://127.0.0.1:5000/api/activate_callback?status=error&message=" . urlencode("Database Error: " . $e->getMessage()));
    exit;
}
?>
