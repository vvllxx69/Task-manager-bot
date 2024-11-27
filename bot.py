import json
import logging
import os
from datetime import datetime, timedelta

from telegram import (
    Update,
    InlineKeyboardButton,
    InlineKeyboardMarkup,
    KeyboardButton,
    ReplyKeyboardMarkup,
    ReplyKeyboardRemove,
)
from telegram.constants import ParseMode
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    CallbackQueryHandler,
    MessageHandler,
    ConversationHandler,
    filters,
    ContextTypes,
)
from sqlalchemy import (
    Column,
    Integer,
    String,
    DateTime,
    ForeignKey,
    Text,
)
from sqlalchemy.orm import relationship, declarative_base, sessionmaker
from sqlalchemy import create_engine
from apscheduler.schedulers.asyncio import AsyncIOScheduler
from apscheduler.triggers.interval import IntervalTrigger
from dotenv import load_dotenv

# Load environment variables from .env file
load_dotenv()

# Configure logging
logging.basicConfig(
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    level=logging.INFO
)
logger = logging.getLogger(__name__)

# Load configuration
with open('config.json', 'r', encoding='utf-8') as f:
    CONFIG = json.load(f)

# Database setup
Base = declarative_base()

class TaskAssignment(Base):
    __tablename__ = 'task_assignments'
    task_id = Column(Integer, ForeignKey('tasks.id'), primary_key=True)
    user_id = Column(Integer, ForeignKey('users.id'), primary_key=True)
    status = Column(String, default='Pending')  # 'Pending', 'Accepted', 'Completed'
    task = relationship('Task', back_populates='assignments')
    user = relationship('User', back_populates='assignments')

class User(Base):
    __tablename__ = 'users'
    id = Column(Integer, primary_key=True)  # Telegram user ID
    username = Column(String, nullable=True, index=True)  # Added username field
    name = Column(String, nullable=False)
    surname = Column(String, nullable=False)
    phone_number = Column(String, unique=True, nullable=False)
    role = Column(String, nullable=False)  # 'rector' or 'staff'
    assignments = relationship('TaskAssignment', back_populates='user')
    comments = relationship('Comment', back_populates='user')

class Task(Base):
    __tablename__ = 'tasks'
    id = Column(Integer, primary_key=True)
    title = Column(String, nullable=False)
    description = Column(Text, nullable=False)
    deadline = Column(DateTime, nullable=False)
    notification_interval = Column(Integer, default=1)  # Interval in minutes before deadline
    assignments = relationship('TaskAssignment', back_populates='task', cascade='all, delete-orphan')
    comments = relationship('Comment', back_populates='task', cascade='all, delete-orphan')

class Comment(Base):
    __tablename__ = 'comments'
    id = Column(Integer, primary_key=True)
    task_id = Column(Integer, ForeignKey('tasks.id'), nullable=False)
    user_id = Column(Integer, ForeignKey('users.id'), nullable=False)
    comment_text = Column(Text, nullable=False)
    timestamp = Column(DateTime, nullable=False)
    task = relationship('Task', back_populates='comments')
    user = relationship('User', back_populates='comments')

# Database connection
DATABASE_URL = "sqlite:///task_manager.db"  # For production, use PostgreSQL or similar
engine = create_engine(DATABASE_URL, echo=False)
SessionLocal = sessionmaker(bind=engine, expire_on_commit=False)
Base.metadata.create_all(bind=engine)

# Initialize Scheduler
scheduler = AsyncIOScheduler()
scheduler.start()

# Utility functions
def get_user(session, user_id, update):
    user = session.query(User).filter(User.id == user_id).first()
    if user:
        # Update username if it has changed
        current_username = update.effective_user.username
        if user.username != current_username:
            user.username = current_username
            session.commit()
    return user

def create_user(session, user_id, username, name, surname, phone_number, role):
    if not username:
        username = f"user_{user_id}"  # Assign a default username if None
    user = User(id=user_id, username=username, name=name, surname=surname, phone_number=phone_number, role=role)
    session.add(user)
    session.commit()
    logger.info(f"Created new user: {name} {surname}, ID: {user_id}, Role: {role}")
    return user

def create_task(session, title, description, deadline, notification_interval, assignee_ids):
    task = Task(title=title, description=description, deadline=deadline, notification_interval=notification_interval)
    session.add(task)
    session.commit()  # Commit to get the task.id

    for assignee_id in assignee_ids:
        user = session.query(User).filter(User.id == assignee_id).first()
        if user:
            assignment = TaskAssignment(task_id=task.id, user_id=user.id)
            session.add(assignment)
    session.commit()
    logger.info(f"Created new task: {title}, Assigned to: {assignee_ids}, Notification Interval: {notification_interval} minutes")
    return task

def add_comment(session, task_id, user_id, comment_text):
    comment = Comment(task_id=task_id, user_id=user_id, comment_text=comment_text, timestamp=datetime.now())
    session.add(comment)
    session.commit()
    logger.info(f"Added comment to task {task_id} by user {user_id}")
    return comment

def schedule_reminder(app, task_id, notification_interval):
    """
    Schedule reminders every `notification_interval` minutes or hours until the task is completed.
    """
    trigger = IntervalTrigger(
        minutes=notification_interval,  # Change to 'hours=notification_interval' if needed
        start_date=datetime.now() + timedelta(seconds=10),  # Start after 10 seconds
    )
    scheduler.add_job(send_reminder, trigger, args=[app, task_id], id=f"reminder_task_{task_id}", replace_existing=True)
    logger.info(f"Scheduled reminders for task {task_id} every {notification_interval} minutes until completion")

async def send_reminder(app, task_id):
    session = SessionLocal()
    task = session.query(Task).filter(Task.id == task_id).first()
    if not task:
        logger.error(f"Task {task_id} not found for reminder.")
        session.close()
        return

    # Check if the task is already completed
    all_completed = all(assignment.status == "Completed" for assignment in task.assignments)
    if all_completed:
        # Remove the scheduled job since the task is completed
        scheduler.remove_job(f"reminder_task_{task_id}")
        logger.info(f"All assignments for task {task_id} are completed. Removed reminder job.")
        session.close()
        return

    for assignment in task.assignments:
        assignee = assignment.user
        if assignment.status != "Completed":
            message_text = CONFIG['reminder_message'].format(title=task.title, deadline=task.deadline.strftime('%Y-%m-%d %H:%M'))
            try:
                await app.bot.send_message(chat_id=assignee.id, text=message_text, parse_mode=ParseMode.MARKDOWN)
                logger.info(f"Sent reminder to user {assignee.id} for task {task_id}.")
            except Exception as e:
                logger.error(f"Error sending reminder to user {assignee.id}: {e}")

    session.close()

async def notify_completion_if_all_completed(app, task_id):
    session = SessionLocal()
    task = session.query(Task).filter(Task.id == task_id).first()
    if not task:
        logger.error(f"Task {task_id} not found for completion notification.")
        session.close()
        return

    # Check if all assignments are completed
    assignments = session.query(TaskAssignment).filter_by(task_id=task_id).all()
    if not assignments:
        logger.warning(f"No assignments found for task {task_id}.")
        session.close()
        return

    if all(assignment.status == "Completed" for assignment in assignments):
        # Remove the scheduled reminder job since the task is completed
        try:
            scheduler.remove_job(f"reminder_task_{task_id}")
            logger.info(f"Removed reminder job for completed task {task_id}.")
        except Exception as e:
            logger.warning(f"No scheduled reminder job found for task {task_id}: {e}")

        # Notify rector (or other relevant role)
        rectors = session.query(User).filter(User.role == "rector").all()
        if not rectors:
            logger.warning("No rectors found to notify about task completion.")
            session.close()
            return

        for rector in rectors:
            try:
                message_text = f"The task *{task.title}* has been completed by all assignees."
                await app.bot.send_message(
                    chat_id=rector.id, text=message_text, parse_mode="Markdown"
                )
                logger.info(f"Notified rector {rector.id} about task {task_id} completion.")
            except Exception as e:
                logger.error(f"Failed to notify rector {rector.id}: {e}")
    else:
        logger.info(f"Task {task_id} is not yet completed by all assignees.")

    session.close()


# Conversation States
(
    REGISTER_CONTACT,
    REGISTER_NAME,
    REGISTER_SURNAME,
    REGISTER_ROLE,
    RECTOR_TASK_TITLE,
    RECTOR_TASK_DESCRIPTION,
    RECTOR_TASK_DEADLINE,
    RECTOR_TASK_NOTIFICATION_INTERVAL,
    ASSIGNMENT_METHOD,
    RECTOR_TASK_ASSIGNEE,
    COMMENT_TASK,
    EDIT_TASK_SELECTION,
    EDIT_TASK_FIELD,
    EDIT_TASK_VALUE,
    CONFIRM_DELETE_TASK,
) = range(15)  

# Handlers
async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = SessionLocal()
    existing_user = get_user(session, user_id, update)
    if existing_user:
        # User is already registered; show appropriate menu
        if existing_user.role == 'rector':
            await show_rector_menu(update, context)
        elif existing_user.role == 'staff':
            await show_staff_menu(update, context)
        else:
            await update.message.reply_text("Your role is not recognized.")
        logger.info(f"User {user_id} is already registered. Displayed {existing_user.role} menu.")
    else:
        # User is not registered; prompt for registration
        keyboard = [
            [InlineKeyboardButton(CONFIG['register_button'], callback_data="register")],
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(CONFIG['welcome_message'], reply_markup=reply_markup)
        logger.info(f"User {user_id} is not registered. Prompted for registration.")
    session.close()

async def register(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = update.effective_user.id
    session = SessionLocal()
    existing_user = get_user(session, user_id, update)
    if existing_user:
        # User is already registered; show appropriate menu
        await query.edit_message_text("You are already registered.", reply_markup=None)
        if existing_user.role == 'rector':
            await show_rector_menu(update, context)
        elif existing_user.role == 'staff':
            await show_staff_menu(update, context)
        else:
            await update.message.reply_text("Your role is not recognized.")
        logger.info(f"User {user_id} is already registered. Displayed {existing_user.role} menu.")
        session.close()
        return ConversationHandler.END
    else:
        # User is not registered; proceed with registration
        reply_markup = ReplyKeyboardMarkup(
            [[KeyboardButton(CONFIG['share_phone_button'], request_contact=True)]],
            one_time_keyboard=True,
            resize_keyboard=True,
        )
        await query.edit_message_text("Please share your phone number using the button below.")
        await update.effective_message.reply_text("Click the button to share your phone number.", reply_markup=reply_markup)
        logger.info(f"User {user_id} initiated registration.")
        session.close()
        return REGISTER_CONTACT

async def handle_contact(update: Update, context: ContextTypes.DEFAULT_TYPE):
    contact = update.message.contact
    phone_number = contact.phone_number
    user_id = update.effective_user.id
    logger.info(f"User {user_id} shared phone number: {phone_number}")

    session = SessionLocal()
    existing_user = session.query(User).filter(User.phone_number == phone_number).first()
    if existing_user:
        if existing_user.id == user_id:
            # Phone number belongs to the user; show appropriate menu
            await update.message.reply_text("You are already registered.", reply_markup=ReplyKeyboardRemove())
            if existing_user.role == 'rector':
                await show_rector_menu(update, context)
            elif existing_user.role == 'staff':
                await show_staff_menu(update, context)
            else:
                await update.message.reply_text("Your role is not recognized.")
            logger.info(f"User {user_id} is already registered with this phone number.")
        else:
            # Phone number is registered to another user
            await update.message.reply_text("This phone number is already registered to another user.", reply_markup=ReplyKeyboardRemove())
            logger.warning(f"Phone number {phone_number} is already registered to a different user.")
        session.close()
        return ConversationHandler.END

    context.user_data['phone_number'] = phone_number
    await update.message.reply_text(CONFIG['enter_name_prompt'], parse_mode=ParseMode.MARKDOWN, reply_markup=ReplyKeyboardRemove())
    logger.info(f"Prompted user {user_id} to enter name.")
    session.close()
    return REGISTER_NAME

async def handle_name(update: Update, context: ContextTypes.DEFAULT_TYPE):
    name = update.message.text.strip()
    if not name:
        await update.message.reply_text("Name cannot be empty. Please enter your Name:")
        logger.warning(f"User {update.effective_user.id} entered empty name.")
        return REGISTER_NAME

    context.user_data['name'] = name
    await update.message.reply_text(CONFIG['enter_surname_prompt'], parse_mode=ParseMode.MARKDOWN)
    logger.info(f"User {update.effective_user.id} entered name: {name}")
    return REGISTER_SURNAME

async def handle_surname(update: Update, context: ContextTypes.DEFAULT_TYPE):
    surname = update.message.text.strip()
    if not surname:
        await update.message.reply_text("Surname cannot be empty. Please enter your Surname:")
        logger.warning(f"User {update.effective_user.id} entered empty surname.")
        return REGISTER_SURNAME

    context.user_data['surname'] = surname
    logger.info(f"User {update.effective_user.id} entered surname: {surname}")

    # Prompt for role selection
    keyboard = [
        [InlineKeyboardButton(CONFIG['role_rector'], callback_data="role_rector")],
        [InlineKeyboardButton(CONFIG['role_staff'], callback_data="role_staff")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(CONFIG['choose_role_prompt'], reply_markup=reply_markup)
    logger.info(f"User {update.effective_user.id} prompted to choose role.")
    return REGISTER_ROLE

async def set_role(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    role = query.data.split("_")[1]
    await query.answer()
    logger.info(f"User {update.effective_user.id} selected role: {role}")

    user_id = update.effective_user.id
    username = update.effective_user.username  # Get the Telegram username
    name = context.user_data.get('name')
    surname = context.user_data.get('surname')
    phone_number = context.user_data.get('phone_number')

    session = SessionLocal()
    existing_user = session.query(User).filter(User.id == user_id).first()
    if existing_user:
        # Update username if it has changed
        if existing_user.username != username:
            existing_user.username = username
            session.commit()
        await query.edit_message_text("You are already registered.", reply_markup=None)
        logger.info(f"User {user_id} is already registered.")
        if existing_user.role == 'rector':
            await show_rector_menu(update, context)
        elif existing_user.role == 'staff':
            await show_staff_menu(update, context)
        else:
            await update.message.reply_text("Your role is not recognized.")
        session.close()
        return ConversationHandler.END

    # Create user
    user = create_user(session, user_id, username, name, surname, phone_number, role)

    await query.edit_message_text(CONFIG['registration_success'].format(role=role.capitalize()))
    logger.info(f"User {user_id} registration successful with role {role}.")
    session.close()

    # Show appropriate menu
    if role == 'rector':
        await show_rector_menu(update, context)
    elif role == 'staff':
        await show_staff_menu(update, context)

    return ConversationHandler.END

def export_user_data_to_txt(session, file_path="user_data.txt"):
    try:
        # Query all users
        users = session.query(User).all()

        if not users:
            print("No user data found in the database.")
            return False

        # Write user data to a text file
        with open(file_path, "w", encoding="utf-8") as file:
            file.write(f"{'Username':<20}{'Phone Number':<20}{'Name':<20}{'Surname':<20}\n")
            file.write("=" * 80 + "\n")

            for user in users:
                username = user.username or "N/A"
                phone_number = user.phone_number
                name = user.name
                surname = user.surname
                file.write(f"{username:<20}{phone_number:<20}{name:<20}{surname:<20}\n")

        print(f"User data successfully exported to {file_path}")
        return True

    except Exception as e:
        print(f"Error exporting user data: {e}")
        return False

# Add this command handler function
async def export_users_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = SessionLocal()
    user = session.query(User).filter(User.id == user_id).first()
    if not user or user.role != 'rector':
        await update.message.reply_text("You are not authorized to use this command.")
        session.close()
        return

    file_path = "user_data.txt"
    success = export_user_data_to_txt(session, file_path)
    if success:
        # Send the file to the user
        with open(file_path, 'rb') as doc:
            await update.message.reply_document(document=doc)
        logger.info(f"User {user_id} exported user data.")
    else:
        await update.message.reply_text("Failed to export user data.")
    session.close()

async def show_rector_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("📋 Task List"), KeyboardButton("🆕 New Task")],
        # [KeyboardButton("📂 Export Users")],  # Removed Export Users button
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    if update.callback_query:
        await update.callback_query.message.reply_text(CONFIG['rector_menu'], parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    else:
        await update.message.reply_text(CONFIG['rector_menu'], parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    logger.info(f"Displayed Rector menu to user {update.effective_user.id}.")

async def show_staff_menu(update: Update, context: ContextTypes.DEFAULT_TYPE):
    keyboard = [
        [KeyboardButton("📋 All Tasks"), KeyboardButton("📝 My Tasks")],
    ]
    reply_markup = ReplyKeyboardMarkup(keyboard, resize_keyboard=True)
    if update.callback_query:
        await update.callback_query.message.reply_text(CONFIG['staff_menu'], parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    else:
        await update.message.reply_text(CONFIG['staff_menu'], parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    logger.info(f"Displayed Staff menu to user {update.effective_user.id}.")

async def rector_task_list(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = SessionLocal()
    tasks = session.query(Task).all()

    if not tasks:
        task_text = CONFIG['no_tasks_available']
        if update.message:
            await update.message.reply_text(task_text)
        elif update.callback_query:
            await update.callback_query.message.edit_text(task_text)
        session.close()
        return

    task_buttons = []
    for task in tasks:
        button = [InlineKeyboardButton(f"{task.title} (ID: {task.id})", callback_data=f"rector_task_{task.id}")]
        task_buttons.append(button)

    reply_markup = InlineKeyboardMarkup(task_buttons)
    if update.message:
        await update.message.reply_text("Select a task:", reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.message.edit_text("Select a task:", reply_markup=reply_markup)

    session.close()

async def rector_task_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    task_id = int(data.split("_")[2])

    session = SessionLocal()
    task = session.query(Task).filter(Task.id == task_id).first()
    if not task:
        await query.edit_message_text("❌ Task not found.")
        session.close()
        return

    # Gather assignees' info
    assignees_info = ""
    for assignment in task.assignments:
        assignee = assignment.user
        status = assignment.status
        assignees_info += f"{assignee.name} {assignee.surname} - {status}\n"

    # Gather comments
    comments_text = ""
    if task.comments:
        comments_text = "\n*Comments:*\n"
        for comment in task.comments:
            commenter = comment.user
            comments_text += f"- {commenter.name} {commenter.surname} [{comment.timestamp.strftime('%Y-%m-%d %H:%M')}]: {comment.comment_text}\n"

    # Display notification interval
    notification_interval = task.notification_interval
    notification_text = f"*Notification Interval:* {notification_interval} minutes\n"

    task_text = (
        f"*ID:* {task.id}\n"
        f"*Title:* {task.title}\n"
        f"*Description:* {task.description}\n"
        f"*Assignees:*\n{assignees_info}"
        f"{notification_text}"
        f"*Deadline:* {task.deadline.strftime('%Y-%m-%d %H:%M')}\n"
        f"{comments_text}"
    )

    keyboard = [
        [InlineKeyboardButton("✏️ Edit", callback_data=f"edit_task_{task.id}"),
         InlineKeyboardButton("🗑️ Delete", callback_data=f"delete_task_{task.id}")],
        [InlineKeyboardButton("🔔 Send Reminder", callback_data=f"remind_task_{task.id}")],
        [InlineKeyboardButton("🔙 Back to Task List", callback_data="back_to_task_list")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(task_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    session.close()

async def rector_new_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.message:
        await update.message.reply_text(CONFIG['create_task_prompt'], parse_mode=ParseMode.MARKDOWN)
    else:
        await update.callback_query.edit_message_text(CONFIG['create_task_prompt'], parse_mode=ParseMode.MARKDOWN)
    logger.info(f"Rector {update.effective_user.id} initiated new task creation.")
    return RECTOR_TASK_TITLE

async def handle_rector_task_title(update: Update, context: ContextTypes.DEFAULT_TYPE):
    title = update.message.text.strip()
    if not title:
        await update.message.reply_text("Title cannot be empty. Please enter the *Title* of the task:", parse_mode=ParseMode.MARKDOWN)
        logger.warning(f"Rector {update.effective_user.id} entered empty task title.")
        return RECTOR_TASK_TITLE

    context.user_data['task_title'] = title
    await update.message.reply_text("📄 Please enter the *Description* of the task:", parse_mode=ParseMode.MARKDOWN)
    logger.info(f"Rector {update.effective_user.id} entered task title: {title}")
    return RECTOR_TASK_DESCRIPTION

async def handle_rector_task_description(update: Update, context: ContextTypes.DEFAULT_TYPE):
    description = update.message.text.strip()
    if not description:
        await update.message.reply_text("Description cannot be empty. Please enter the *Description* of the task:", parse_mode=ParseMode.MARKDOWN)
        logger.warning(f"Rector {update.effective_user.id} entered empty task description.")
        return RECTOR_TASK_DESCRIPTION

    context.user_data['task_description'] = description
    await update.message.reply_text("⏰ Please enter the *Deadline* in the format `YYYY-MM-DD HH:MM` (e.g., 2024-12-31 23:59):", parse_mode=ParseMode.MARKDOWN)
    logger.info(f"Rector {update.effective_user.id} entered task description.")
    return RECTOR_TASK_DEADLINE

async def handle_rector_task_deadline(update: Update, context: ContextTypes.DEFAULT_TYPE):
    deadline_str = update.message.text.strip()
    try:
        deadline = datetime.strptime(deadline_str, "%Y-%m-%d %H:%M")
    except ValueError:
        await update.message.reply_text(CONFIG.get('invalid_deadline', "Invalid deadline format."), parse_mode=ParseMode.MARKDOWN)
        logger.warning(f"Rector {update.effective_user.id} entered invalid deadline format: {deadline_str}")
        return RECTOR_TASK_DEADLINE

    context.user_data['task_deadline'] = deadline
    await update.message.reply_text("⏰ Please enter the *Notification Interval* in minutes (e.g., 1):", parse_mode=ParseMode.MARKDOWN)
    logger.info(f"Rector {update.effective_user.id} entered task deadline: {deadline_str}")
    return RECTOR_TASK_NOTIFICATION_INTERVAL

async def handle_rector_task_notification_interval(update: Update, context: ContextTypes.DEFAULT_TYPE):
    interval_str = update.message.text.strip()
    user_id = update.effective_user.id

    if not interval_str.isdigit():
        await update.message.reply_text(CONFIG.get('invalid_notification_interval', "❌ Please enter a valid number of minutes (e.g., 1)."))
        logger.warning(f"Rector {user_id} entered invalid notification interval: {interval_str}")
        return RECTOR_TASK_NOTIFICATION_INTERVAL

    interval = int(interval_str)
    if interval <= 0:
        await update.message.reply_text(CONFIG.get('invalid_notification_interval', "❌ Notification interval must be a positive number of minutes."))
        logger.warning(f"Rector {user_id} entered non-positive notification interval: {interval}")
        return RECTOR_TASK_NOTIFICATION_INTERVAL

    context.user_data['task_notification_interval'] = interval
    logger.info(f"Rector {user_id} set notification interval: {interval} minutes")

    await update.message.reply_text(CONFIG.get('notification_interval_set', "✅ Notification interval set successfully."))

    # Proceed to assignment method
    keyboard = [
        [InlineKeyboardButton("📌 Assign to Someone", callback_data="assign_specific")],
        [InlineKeyboardButton("🌐 Assign to All Staff", callback_data="assign_all")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(CONFIG.get('choose_assignment_method', "Please choose assignment method:"), reply_markup=reply_markup)
    return ASSIGNMENT_METHOD

async def assign_staff_selection(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    staff_id = int(query.data.split("_")[2])
    selected_staff_ids = context.user_data.get('selected_staff_ids', [])

    if staff_id not in selected_staff_ids:
        selected_staff_ids.append(staff_id)
    else:
        selected_staff_ids.remove(staff_id)

    context.user_data['selected_staff_ids'] = selected_staff_ids

    # Update the message with the current selection
    session = SessionLocal()
    staff = session.query(User).filter(User.id == staff_id).first()
    session.close()

    message_text = f"{staff.username or staff.name} {staff.surname} {'selected' if staff_id in selected_staff_ids else 'deselected'}"
    await query.answer(message_text)

async def set_assignment_method(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    choice = query.data
    await query.answer()

    if choice == "assign_specific":
        session = SessionLocal()
        staff_members = session.query(User).filter(User.role == 'staff').all()
        session.close()

        if not staff_members:
            await query.edit_message_text("❌ No staff members found to assign the task.")
            logger.error(f"No staff members found for task assignment by Rector {update.effective_user.id}.")
            return ConversationHandler.END

        # Create inline buttons for all staff usernames
        buttons = [
            [InlineKeyboardButton(f"{staff.username or staff.name} {staff.surname}", callback_data=f"assign_staff_{staff.id}")]
            for staff in staff_members
        ]
        buttons.append([InlineKeyboardButton("✅ Confirm Selection", callback_data="assign_confirm")])
        reply_markup = InlineKeyboardMarkup(buttons)

        await query.edit_message_text("👤 Select staff members to assign the task:", reply_markup=reply_markup)
        context.user_data['selected_staff_ids'] = []  # Initialize selected IDs
        return RECTOR_TASK_ASSIGNEE

    elif choice == "assign_all":
        # Assign to all staff members
        session = SessionLocal()
        staff_members = session.query(User).filter(User.role == 'staff').all()
        session.close()

        if not staff_members:
            await query.edit_message_text("❌ No staff members found to assign the task.")
            return ConversationHandler.END

        assignee_ids = [staff.id for staff in staff_members]
        title = context.user_data.get('task_title')
        description = context.user_data.get('task_description')
        deadline = context.user_data.get('task_deadline')
        notification_interval = context.user_data.get('task_notification_interval', 1)  # Default to 1 minute

        task = create_task(session, title, description, deadline, notification_interval, assignee_ids)

        # Schedule reminders based on notification_interval
        schedule_reminder(context.application, task.id, notification_interval)

        await query.edit_message_text(CONFIG['task_created'].format(title=title, assignee="All Staff Members"))
        return ConversationHandler.END

async def handle_rector_task_assignee(update: Update, context: ContextTypes.DEFAULT_TYPE):
    assignee_input = update.message.text.strip()
    session = SessionLocal()

    # Attempt to find user by username, ID, or full name
    assignee = None
    if assignee_input.startswith('@'):
        username = assignee_input[1:]
        assignee = session.query(User).filter(
            User.username.ilike(username), User.role == 'staff'
        ).first()
    else:
        try:
            assignee_id = int(assignee_input)
            assignee = session.query(User).filter(User.id == assignee_id, User.role == 'staff').first()
        except ValueError:
            # Try to search by name and surname
            name_parts = assignee_input.split()
            if len(name_parts) == 2:
                first_name, last_name = name_parts
                assignee = session.query(User).filter(
                    User.name.ilike(first_name), User.surname.ilike(last_name), User.role == 'staff'
                ).first()
            else:
                assignee = None

    if not assignee:
        await update.message.reply_text(
            "❌ Assignee not found or not a staff member. Please enter a valid *Assignee's* Telegram username (e.g., @username), ID, or full name:",
            parse_mode=ParseMode.MARKDOWN
        )
        logger.warning(f"Rector {update.effective_user.id} entered invalid assignee: {assignee_input}")
        session.close()
        return RECTOR_TASK_ASSIGNEE

    # Retrieve notification_interval
    notification_interval = context.user_data.get('task_notification_interval', 1)  # Default to 1 minute

    # Create the task and assign to the specific user
    title = context.user_data.get('task_title')
    description = context.user_data.get('task_description')
    deadline = context.user_data.get('task_deadline')
    task = create_task(session, title, description, deadline, notification_interval, [assignee.id])

    # Schedule reminders based on notification_interval
    schedule_reminder(context.application, task.id, notification_interval)

    await update.message.reply_text(
        CONFIG['task_created'].format(title=title, assignee=f"{assignee.name} {assignee.surname}"),
        parse_mode=ParseMode.MARKDOWN
    )
    logger.info(f"Rector {update.effective_user.id} created task '{title}' assigned to {assignee.id} with notification interval {notification_interval} minutes")
    session.close()

    # Show Rector Menu
    await show_rector_menu(update, context)
    return ConversationHandler.END

async def assign_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    selected_staff_ids = context.user_data.get('selected_staff_ids', [])
    if not selected_staff_ids:
        await query.edit_message_text("❌ No staff members selected.")
        return ConversationHandler.END

    session = SessionLocal()
    title = context.user_data.get('task_title')
    description = context.user_data.get('task_description')
    deadline = context.user_data.get('task_deadline')
    notification_interval = context.user_data.get('task_notification_interval', 1)  # Default to 1 minute

    task = create_task(session, title, description, deadline, notification_interval, selected_staff_ids)
    session.close()

    # Schedule reminders based on notification_interval
    schedule_reminder(context.application, task.id, notification_interval)

    await query.edit_message_text(CONFIG['task_created'].format(title=title, assignee="Selected Staff Members"))
    return ConversationHandler.END

async def cancel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.callback_query:
        await update.callback_query.answer()
        await update.callback_query.message.reply_text(CONFIG['operation_cancelled'], reply_markup=ReplyKeyboardRemove())
    else:
        await update.message.reply_text(CONFIG['operation_cancelled'], reply_markup=ReplyKeyboardRemove())
    logger.info(f"User {update.effective_user.id} cancelled the operation.")
    return ConversationHandler.END

# Rector Edit Task Handlers
async def edit_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split("_")[2])
    context.user_data['edit_task_id'] = task_id

    keyboard = [
        [InlineKeyboardButton("📝 Title", callback_data="edit_field_title")],
        [InlineKeyboardButton("📄 Description", callback_data="edit_field_description")],
        [InlineKeyboardButton("⏰ Deadline", callback_data="edit_field_deadline")],
        [InlineKeyboardButton("🔔 Notification Interval", callback_data="edit_field_notification_interval")],  # New option
        [InlineKeyboardButton("🔙 Back", callback_data=f"rector_task_{task_id}")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Select the field you want to edit:", reply_markup=reply_markup)
    return EDIT_TASK_FIELD

async def edit_task_field(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    field = query.data.split("_")[2]
    context.user_data['edit_task_field'] = field

    if field == 'notification_interval':
        await query.edit_message_text("Please enter the new *Notification Interval* in minutes (e.g., 1):", parse_mode=ParseMode.MARKDOWN)
    else:
        await query.edit_message_text(f"Please enter the new value for *{field.capitalize()}*:", parse_mode=ParseMode.MARKDOWN)

    return EDIT_TASK_VALUE

async def edit_task_value(update: Update, context: ContextTypes.DEFAULT_TYPE):
    new_value = update.message.text.strip()
    task_id = context.user_data.get('edit_task_id')
    field = context.user_data.get('edit_task_field')

    session = SessionLocal()
    task = session.query(Task).filter(Task.id == task_id).first()
    if not task:
        await update.message.reply_text("❌ Task not found.")
        session.close()
        return ConversationHandler.END

    if field == 'title':
        task.title = new_value
    elif field == 'description':
        task.description = new_value
    elif field == 'deadline':
        try:
            task.deadline = datetime.strptime(new_value, "%Y-%m-%d %H:%M")
        except ValueError:
            await update.message.reply_text(CONFIG.get('invalid_deadline', "Invalid deadline format."), parse_mode=ParseMode.MARKDOWN)
            logger.warning(f"Rector {update.effective_user.id} entered invalid deadline format: {new_value}")
            session.close()
            return EDIT_TASK_VALUE
    elif field == 'notification_interval':
        if not new_value.isdigit():
            await update.message.reply_text("❌ Please enter a valid number of minutes (e.g., 1).")
            logger.warning(f"Rector {update.effective_user.id} entered invalid notification interval: {new_value}")
            session.close()
            return EDIT_TASK_VALUE

        interval = int(new_value)
        if interval <= 0:
            await update.message.reply_text("❌ Notification interval must be a positive number of minutes.")
            logger.warning(f"Rector {update.effective_user.id} entered non-positive notification interval: {interval}")
            session.close()
            return EDIT_TASK_VALUE

        task.notification_interval = interval
        logger.info(f"Task {task_id} notification interval updated to {interval} minutes")

        # Reschedule the reminder
        try:
            scheduler.remove_job(f"reminder_task_{task.id}")  # Remove existing job
            logger.info(f"Removed existing reminder job for task {task.id} during edit.")
        except Exception as e:
            logger.warning(f"No existing reminder job found for task {task.id}: {e}")

        schedule_reminder(context.application, task.id, interval)

    else:
        await update.message.reply_text("❌ Invalid field.")
        session.close()
        return ConversationHandler.END

    session.commit()
    await update.message.reply_text(f"✅ Task *{field.capitalize()}* updated successfully.", parse_mode=ParseMode.MARKDOWN)
    logger.info(f"Task {task_id} updated by Rector {update.effective_user.id}. Field: {field}, New Value: {new_value}")
    session.close()

    # Show Rector Menu
    await show_rector_menu(update, context)
    return ConversationHandler.END

# Rector Delete Task Handlers
async def delete_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split("_")[2])
    context.user_data['delete_task_id'] = task_id

    keyboard = [
        [InlineKeyboardButton("✅ Yes", callback_data="confirm_delete_task")],
        [InlineKeyboardButton("❌ No", callback_data=f"rector_task_{task_id}")],
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text("Are you sure you want to delete this task?", reply_markup=reply_markup)
    return CONFIRM_DELETE_TASK

async def confirm_delete_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = context.user_data.get('delete_task_id')

    session = SessionLocal()
    task = session.query(Task).filter(Task.id == task_id).first()
    if not task:
        await query.edit_message_text("❌ Task not found.")
        session.close()
        return ConversationHandler.END

    # Remove the scheduled reminder job if exists
    try:
        scheduler.remove_job(f"reminder_task_{task.id}")
        logger.info(f"Removed reminder job for deleted task {task.id}.")
    except Exception as e:
        logger.warning(f"No scheduled reminder job found for task {task.id}: {e}")

    session.delete(task)
    session.commit()
    await query.edit_message_text("🗑️ Task deleted successfully.")
    logger.info(f"Task {task_id} deleted by Rector {update.effective_user.id}.")
    session.close()

    # Show Rector Menu
    await show_rector_menu(update, context)
    return ConversationHandler.END

async def confirm_delete_after_completion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split("_")[2])

    session = SessionLocal()
    task = session.query(Task).filter(Task.id == task_id).first()
    if not task:
        await query.edit_message_text("❌ Task not found.")
        session.close()
        return

    # Remove the scheduled reminder job if exists
    try:
        scheduler.remove_job(f"reminder_task_{task.id}")
        logger.info(f"Removed reminder job for deleted task {task.id}.")
    except Exception as e:
        logger.warning(f"No scheduled reminder job found for task {task.id}: {e}")

    # Delete the task
    session.delete(task)
    session.commit()
    await query.edit_message_text(f"🗑️ Task *{task.title}* has been deleted successfully.")
    logger.info(f"Task {task_id} deleted by creator after completion.")
    session.close()

async def keep_task_after_completion(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split("_")[2])

    session = SessionLocal()
    task = session.query(Task).filter(Task.id == task_id).first()
    if not task:
        await query.edit_message_text("❌ Task not found.")
        session.close()
        return

    await query.edit_message_text(f"✅ Task *{task.title}* has been retained.")
    logger.info(f"Task {task_id} retained by creator after completion.")
    session.close()

# Rector Send Reminder
async def send_reminder_to_assignees(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    task_id = int(query.data.split("_")[2])

    session = SessionLocal()
    task = session.query(Task).filter(Task.id == task_id).first()
    if not task:
        await query.edit_message_text("❌ Task not found.")
        session.close()
        return

    for assignment in task.assignments:
        assignee = assignment.user
        if assignment.status != "Completed":
            message_text = CONFIG['reminder_message'].format(title=task.title, deadline=task.deadline.strftime('%Y-%m-%d %H:%M'))
            try:
                await context.application.bot.send_message(chat_id=assignee.id, text=message_text, parse_mode=ParseMode.MARKDOWN)
                logger.info(f"Sent reminder to user {assignee.id} for task {task_id}.")
            except Exception as e:
                logger.error(f"Error sending reminder to user {assignee.id}: {e}")

    await query.edit_message_text("🔔 Reminder sent to assignees.")
    session.close()

# Staff Handlers
async def staff_all_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    session = SessionLocal()
    tasks = session.query(Task).all()

    if not tasks:
        task_text = CONFIG['no_tasks_available']
        if update.message:
            await update.message.reply_text(task_text)
        elif update.callback_query:
            await update.callback_query.message.edit_text(task_text)
        session.close()
        return

    task_buttons = []
    for task in tasks:
        button = [InlineKeyboardButton(f"{task.title} (ID: {task.id})", callback_data=f"staff_task_{task.id}")]
        task_buttons.append(button)

    reply_markup = InlineKeyboardMarkup(task_buttons)
    if update.message:  # Case for message interactions
        await update.message.reply_text("Select a task:", reply_markup=reply_markup)
    elif update.callback_query:  # Case for callback interactions
        await update.callback_query.message.edit_text("Select a task:", reply_markup=reply_markup)

    session.close()

async def staff_task_action(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    task_id = int(data.split("_")[2])

    session = SessionLocal()
    task = session.query(Task).filter(Task.id == task_id).first()
    if not task:
        await query.edit_message_text("❌ Task not found.")
        session.close()
        return

    assignment = session.query(TaskAssignment).filter_by(task_id=task_id, user_id=update.effective_user.id).first()
    if not assignment:
        status = "Not Assigned"
    else:
        status = assignment.status

    task_text = (
        f"*ID:* {task.id}\n"
        f"*Title:* {task.title}\n"
        f"*Description:* {task.description}\n"
        f"*Deadline:* {task.deadline.strftime('%Y-%m-%d %H:%M')}\n"
        f"*Status:* {status}\n"
    )

    keyboard = []
    if assignment:
        keyboard.append([
            InlineKeyboardButton("✅ Accept", callback_data=f"accept_task_{task.id}"),
            InlineKeyboardButton("✔️ Complete", callback_data=f"complete_task_{task.id}"),
        ])
        keyboard.append([
            InlineKeyboardButton("💬 Comment", callback_data=f"comment_task_{task.id}"),
        ])
    keyboard.append([InlineKeyboardButton("🔙 Back to Task List", callback_data="back_to_staff_task_list")])
    reply_markup = InlineKeyboardMarkup(keyboard)
    await query.edit_message_text(task_text, parse_mode=ParseMode.MARKDOWN, reply_markup=reply_markup)
    session.close()

async def staff_my_tasks(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    session = SessionLocal()
    user = session.query(User).filter(User.id == user_id, User.role == 'staff').first()
    if not user:
        await update.message.reply_text("⚠️ User not found or not authorized.", parse_mode=ParseMode.MARKDOWN)
        logger.warning(f"User {user_id} not found or not authorized as staff.")
        session.close()
        return

    assignments = session.query(TaskAssignment).filter_by(user_id=user_id).all()

    if not assignments:
        task_text = CONFIG['no_tasks_assigned']
        await update.message.reply_text(task_text)
        session.close()
        return

    task_buttons = []
    for assignment in assignments:
        task = assignment.task
        button = [InlineKeyboardButton(f"{task.title} (ID: {task.id})", callback_data=f"staff_task_{task.id}")]
        task_buttons.append(button)

    reply_markup = InlineKeyboardMarkup(task_buttons)
    await update.message.reply_text("Select a task:", reply_markup=reply_markup)
    session.close()

async def accept_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    task_id = int(data.split("_")[2])

    session = SessionLocal()
    user_id = update.effective_user.id

    assignment = session.query(TaskAssignment).filter_by(task_id=task_id, user_id=user_id).first()
    if not assignment:
        await query.edit_message_text("⚠️ You are not assigned to this task.", parse_mode=ParseMode.MARKDOWN)
        logger.warning(f"User {user_id} is not assigned to task {task_id}.")
        session.close()
        return

    if assignment.status == 'Pending':
        assignment.status = 'Accepted'
        session.commit()
        await query.edit_message_text(CONFIG['task_accepted'].format(title=assignment.task.title), parse_mode=ParseMode.MARKDOWN)
        logger.info(f"Task {task_id} accepted by user {user_id}.")

        # Show the staff member's tasks
        await staff_my_tasks(update, context)
    else:
        await query.edit_message_text(CONFIG['task_already_accepted'].format(title=assignment.task.title), parse_mode=ParseMode.MARKDOWN)
        logger.info(f"Task {task_id} already accepted by user {user_id}.")
    session.close()

async def complete_task(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    task_id = int(data.split("_")[2])

    session = SessionLocal()
    user_id = update.effective_user.id

    assignment = session.query(TaskAssignment).filter_by(task_id=task_id, user_id=user_id).first()
    if not assignment:
        await query.edit_message_text("⚠️ You are not assigned to this task.", parse_mode="Markdown")
        logger.warning(f"User {user_id} is not assigned to task {task_id}.")
        session.close()
        return

    if assignment.status != "Completed":
        assignment.status = "Completed"
        session.commit()
        await query.edit_message_text(f"✅ You have completed the task *{assignment.task.title}*.", parse_mode="Markdown")
        logger.info(f"Task {task_id} completed by user {user_id}.")

        # Notify if all assignees have completed
        await notify_completion_if_all_completed(context.application, task_id)
    else:
        await query.edit_message_text(f"⚠️ Task *{assignment.task.title}* is already marked as completed.", parse_mode="Markdown")
        logger.info(f"Task {task_id} already marked as completed by user {user_id}.")
    session.close()

async def comment_task_start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()

    data = query.data
    task_id = int(data.split("_")[2])

    session = SessionLocal()
    assignment = session.query(TaskAssignment).filter_by(task_id=task_id, user_id=update.effective_user.id).first()
    if not assignment:
        await query.edit_message_text("⚠️ You are not assigned to this task.", parse_mode=ParseMode.MARKDOWN)
        logger.warning(f"User {update.effective_user.id} is not assigned to task {task_id}.")
        session.close()
        return ConversationHandler.END

    context.user_data['comment_task_id'] = task_id
    await query.message.reply_text(CONFIG['comment_prompt'], parse_mode=ParseMode.MARKDOWN)
    logger.info(f"User {update.effective_user.id} started commenting on task {task_id}.")
    session.close()
    return COMMENT_TASK

async def handle_comment(update: Update, context: ContextTypes.DEFAULT_TYPE):
    comment_text = update.message.text.strip()
    if not comment_text:
        await update.message.reply_text("💬 Comment cannot be empty. Please enter your comment:")
        logger.warning(f"User {update.effective_user.id} entered empty comment.")
        return COMMENT_TASK

    task_id = context.user_data.get('comment_task_id')
    user_id = update.effective_user.id

    session = SessionLocal()
    assignment = session.query(TaskAssignment).filter_by(task_id=task_id, user_id=user_id).first()
    if not assignment:
        await update.message.reply_text("⚠️ You are not assigned to this task.", parse_mode=ParseMode.MARKDOWN)
        logger.warning(f"User {user_id} is not assigned to task {task_id}.")
        session.close()
        return ConversationHandler.END

    # Add the comment
    comment = add_comment(session, task_id, user_id, comment_text)
    await update.message.reply_text(CONFIG['comment_added'], parse_mode=ParseMode.MARKDOWN)
    logger.info(f"User {user_id} added comment to task {task_id}.")

    # Notify Rector(s)
    rectors = session.query(User).filter(User.role == 'rector').all()
    commenter = session.query(User).filter(User.id == user_id).first()
    task = session.query(Task).filter(Task.id == task_id).first()
    for rector in rectors:
        message_text = f"💬 New comment on task *{task.title}* by {commenter.name} {commenter.surname}:\n\n{comment_text}"
        try:
            await context.application.bot.send_message(chat_id=rector.id, text=message_text, parse_mode=ParseMode.MARKDOWN)
            logger.info(f"Sent comment notification to Rector {rector.id} for task {task_id}.")
        except Exception as e:
            logger.error(f"Error sending comment notification to Rector {rector.id}: {e}")

    session.close()

    return ConversationHandler.END

# Main Function
def main():
    # Initialize the bot application
    BOT_TOKEN = os.getenv("BOT_TOKEN")
    if not BOT_TOKEN:
        logger.error("Bot token not found. Please set the BOT_TOKEN environment variable.")
        return

    app = ApplicationBuilder().token(BOT_TOKEN).build()

    # Register /start command handler
    app.add_handler(CommandHandler("start", start))

    # Registration Conversation Handler
    registration_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(register, pattern="^register$")],
        states={
            REGISTER_CONTACT: [MessageHandler(filters.CONTACT, handle_contact)],
            REGISTER_NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_name)],
            REGISTER_SURNAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_surname)],
            REGISTER_ROLE: [CallbackQueryHandler(set_role, pattern="^role_.*")]
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(registration_conv)

    # Rector Task Creation Conversation Handler
    rector_task_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.TEXT & filters.Regex("^🆕 New Task$"), rector_new_task)],
        states={
            RECTOR_TASK_TITLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_rector_task_title)],
            RECTOR_TASK_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_rector_task_description)],
            RECTOR_TASK_DEADLINE: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_rector_task_deadline)],
            RECTOR_TASK_NOTIFICATION_INTERVAL: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_rector_task_notification_interval)],  # New handler
            ASSIGNMENT_METHOD: [CallbackQueryHandler(set_assignment_method, pattern="^(assign_specific|assign_all)$")],
            RECTOR_TASK_ASSIGNEE: [
                CallbackQueryHandler(assign_staff_selection, pattern="^assign_staff_\\d+$"),
                CallbackQueryHandler(assign_confirm, pattern="^assign_confirm$")
            ],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(rector_task_conv)

    # Rector Edit Task Conversation Handler
    edit_task_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(edit_task_start, pattern="^edit_task_\\d+$")],
        states={
            EDIT_TASK_FIELD: [CallbackQueryHandler(edit_task_field, pattern="^edit_field_.*$")],
            EDIT_TASK_VALUE: [MessageHandler(filters.TEXT & ~filters.COMMAND, edit_task_value)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(edit_task_conv)

    # Rector Delete Task Conversation Handler
    delete_task_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(delete_task_start, pattern="^delete_task_\\d+$")],
        states={
            CONFIRM_DELETE_TASK: [CallbackQueryHandler(confirm_delete_task, pattern="^confirm_delete_task$")],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(delete_task_conv)

    # Comment Conversation Handler
    comment_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(comment_task_start, pattern="^comment_task_\\d+$")],
        states={
            COMMENT_TASK: [MessageHandler(filters.TEXT & ~filters.COMMAND, handle_comment)],
        },
        fallbacks=[CommandHandler("cancel", cancel)],
    )
    app.add_handler(comment_conv)

    # Rector Task List Handler
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^📋 Task List$"), rector_task_list))
    app.add_handler(CallbackQueryHandler(rector_task_action, pattern="^rector_task_\\d+$"))
    app.add_handler(CallbackQueryHandler(lambda update, context: rector_task_list(update, context), pattern="^back_to_task_list$"))

    # Rector Send Reminder Handler
    app.add_handler(CallbackQueryHandler(send_reminder_to_assignees, pattern="^remind_task_\\d+$"))

    # Staff All Tasks Handler
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^📋 All Tasks$"), staff_all_tasks))
    app.add_handler(CallbackQueryHandler(staff_task_action, pattern="^staff_task_\\d+$"))
    app.add_handler(CallbackQueryHandler(lambda update, context: staff_all_tasks(update, context), pattern="^back_to_staff_task_list$"))

    # Staff My Tasks Handler
    app.add_handler(MessageHandler(filters.TEXT & filters.Regex("^📝 My Tasks$"), staff_my_tasks))

    # Accept Task Handler
    app.add_handler(CallbackQueryHandler(accept_task, pattern="^accept_task_\\d+$"))

    # Complete Task Handler
    app.add_handler(CallbackQueryHandler(complete_task, pattern="^complete_task_\\d+$"))

    # Comment Task Handler
    app.add_handler(CallbackQueryHandler(comment_task_start, pattern="^comment_task_\\d+$"))

    # Menu Handlers
    app.add_handler(CallbackQueryHandler(show_rector_menu, pattern="^menu$"))
    app.add_handler(CallbackQueryHandler(show_staff_menu, pattern="^menu$"))

    # Confirm task deletion
    app.add_handler(CallbackQueryHandler(confirm_delete_after_completion, pattern="^delete_task_\\d+_confirm$"))

    # Retain the task
    app.add_handler(CallbackQueryHandler(keep_task_after_completion, pattern="^keep_task_\\d+_confirm$"))

    # Export Users Handler
    app.add_handler(CommandHandler("export_users", export_users_handler))

    # Start the bot
    logger.info("Bot is running...")
    print("Bot is running...")
    app.run_polling()

if __name__ == "__main__":
    main()




    ##





