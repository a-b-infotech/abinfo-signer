-- ====================================================================
-- A B INFOTECH - License Keys Table Update for License Variants / Tiers
-- Run this in phpMyAdmin or MySQL console for database: ezyro_37092101_fj
-- ====================================================================

-- 1. Add variant columns: license_tier ('basic', 'pro', 'enterprise'), ocr_enabled, and edit_enabled
ALTER TABLE `license_keys`
ADD COLUMN `license_tier` ENUM('basic', 'pro', 'enterprise') NOT NULL DEFAULT 'pro' AFTER `status`,
ADD COLUMN `ocr_enabled` TINYINT(1) NOT NULL DEFAULT 1 AFTER `license_tier`,
ADD COLUMN `edit_enabled` TINYINT(1) NOT NULL DEFAULT 1 AFTER `ocr_enabled`;

-- 2. Ensure all existing keys are marked as Pro with OCR & Edit unlocked
UPDATE `license_keys`
SET `license_tier` = 'pro',
    `ocr_enabled` = 1,
    `edit_enabled` = 1
WHERE `license_tier` IS NULL OR `license_tier` = '';

-- ====================================================================
-- EXAMPLES FOR CREATING NEW LICENSE KEYS:
-- ====================================================================

-- Example A: Create a "Basic" License (Single/Bulk PDF Signer, Stamp, Compress, Merge - OCR & Edit LOCKED)
-- INSERT INTO `license_keys` (`license_key`, `status`, `license_tier`, `ocr_enabled`, `edit_enabled`, `client_email`, `expiredate`)
-- VALUES ('ABI-BASIC-998877', 'active', 'basic', 0, 0, 'client@example.com', '2027-12-31 23:59:59');

-- Example B: Create a "Pro" License (Full Suite with OCR & Text Edit UNLOCKED)
-- INSERT INTO `license_keys` (`license_key`, `status`, `license_tier`, `ocr_enabled`, `edit_enabled`, `client_email`, `expiredate`)
-- VALUES ('ABI-PRO-112233', 'active', 'pro', 1, 1, 'client@example.com', '2027-12-31 23:59:59');

-- Example C: Create an "Enterprise" License (All features, unlimited)
-- INSERT INTO `license_keys` (`license_key`, `status`, `license_tier`, `ocr_enabled`, `edit_enabled`, `client_email`, `expiredate`)
-- VALUES ('ABI-ENT-445566', 'active', 'enterprise', 1, 1, 'client@example.com', '2030-12-31 23:59:59');
