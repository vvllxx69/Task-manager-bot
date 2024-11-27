# Task Manager Bot

### Developers
- [Shoxrux Abdumovlonov](https://github.com/vvllxx69)
- [Komron Asadullaxojaev](https://github.com/en1gma0)

---

## Overview

The Task Manager Bot is a Telegram bot designed to streamline task assignment and management between a Rector and Staff members. The bot provides an intuitive and interactive interface to help users assign, track, and update tasks efficiently. It's a perfect solution for managing organizational tasks with ease and transparency.

---

to use notification interval 
pip install alembic
alembic init alembic
alembic revision --autogenerate -m
alembic upgrade head

## Features

### Rector
- **Task Creation**: Add new tasks with details like title, description, deadline, and assigned users.
- **Task List**: View all tasks and their statuses.
- **Edit Tasks**: Update the task description, title, users, or deadline as needed.
- **Delete Tasks**: Remove tasks that are no longer relevant.
- **Reminders**: Send task reminders to assigned staff.
- **Status Tracking**: Monitor the progress of tasks, including Accepted, Completed, or Pending statuses.

### Staff
- **Task List**: View all assigned tasks.
- **My Tasks**: Check personal task assignments.
- **Accept Tasks**: Acknowledge assigned tasks.
- **Complete Tasks**: Mark tasks as completed when done.
- **Commenting**: Add comments or feedback to tasks for clarification.

### Additional Features
- **Interactive Menus**: Uses inline buttons and command triggers for smooth interaction.
- **Real-time Updates**: Dynamic updates for task statuses and deadlines.

---

## Getting Started

To use the Task Manager Bot, follow these steps:

### Installation
1. Clone the repository:
   \`\`\`bash
   git clone https://github.com/username/task-manager-bot.git
   cd task-manager-bot
   \`\`\`

2. Install the required dependencies:
   \`\`\`bash
   pip install -r requirements.txt
   \`\`\`

### Configuration
1. Set up your Telegram bot token in a configuration file (e.g., \`constants.py\` or \`.env\`):
   - **TOKEN**: The Telegram bot token from [BotFather](https://core.telegram.org/bots#botfather).
   - **DEFAULT_ADMIN**: Telegram ID of the Rector or administrator.

2. Set up the database:
   \`\`\`bash
   python database.py
   \`\`\`

### Run the Bot
Start the bot by running the main script:
\`\`\`bash
python bot.py
\`\`\`




## Dependencies

The project uses the following Python libraries:
- **python-telegram-bot**
- **SQLAlchemy**



### Rector Commands:
- **/start**: Register as the Rector and access the task management menu.
- **Task List**: View all tasks and their statuses.
- **New Task**: Create a new task and assign it to staff members.
- **Edit/Delete**: Modify or remove existing tasks.
- **Reminders**: Send reminders for pending tasks.

### Staff Commands:
- **/start**: Register as Staff and access your personal task menu.
- **My Tasks**: View tasks assigned to you.
- **Accept**: Accept assigned tasks.
- **Complete**: Mark tasks as completed.
- **Comment**: Add feedback or notes to tasks.

---

## Contributing

If you find issues or have suggestions for improvements, feel free to create an issue or submit a pull request.

---

## Acknowledgments

Special thanks to:
- The developers of the \`python-telegram-bot\` and \`SQLAlchemy\` libraries for providing the core functionality.
- The task management framework was inspired by real-world organizational needs.

---

Enjoy organizing your tasks with the Task Manager Bot!
