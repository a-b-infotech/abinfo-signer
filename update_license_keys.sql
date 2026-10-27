-- ====================================================================
-- A B INFOTECH - Update license_keys Table: Basic & Pro Editions
-- Run this in phpMyAdmin or MySQL console for database: ezyro_37092101_fj
-- ====================================================================

-- 1. Remove old ocr_enabled and edit_enabled columns if they exist
ALTER TABLE `license_keys`
DROP COLUMN IF EXISTS `ocr_enabled`,
DROP COLUMN IF EXISTS `edit_enabled`;

-- 2. Modify license_tier to strictly ENUM('basic', 'pro') defaulting to 'pro'
ALTER TABLE `license_keys`
MODIFY COLUMN `license_tier` ENUM('basic', 'pro') NOT NULL DEFAULT 'pro';

-- 3. Ensure all existing keys default to 'pro'
UPDATE `license_keys`
SET `license_tier` = 'pro'
WHERE `license_tier` NOT IN ('basic', 'pro') OR `license_tier` IS NULL;

-- ====================================================================
-- EXAMPLES FOR CREATING NEW LICENSE KEYS:
-- ====================================================================

-- Example 1: Create a "basic" License (Digital Signatures, Compress, Merge, Verify, Tools. NO OCR/Edit, Standard Signing without custom settings)
-- INSERT INTO `license_keys` (`license_key`, `status`, `license_tier`, `client_email`, `expiredate`)
-- VALUES ('ABI-BASIC-1001', 'active', 'basic', 'client@example.com', '2027-12-31 23:59:59');

-- Example 2: Create a "pro" License (Full Suite + Wondershare/Adobe-style PDF In-Place Editor & OCR + Custom Signature Logo/Password/Settings)
-- INSERT INTO `license_keys` (`license_key`, `status`, `license_tier`, `client_email`, `expiredate`)
-- VALUES ('ABI-PRO-2001', 'active', 'pro', 'client@example.com', '2027-12-31 23:59:59');
