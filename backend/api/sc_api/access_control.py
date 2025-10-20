import json
import os

class AccessControl:

    status_order = ['in development', 'ready', 'assigned', 'in review', 'published']
    
    def __init__(self, base_folder:str):
        self.base_folder = base_folder
        self.file_path =  os.path.join(base_folder, "config/config.json")
        self.load_config()

    def load_config(self):
        with open(self.file_path) as f:
            self.config = json.load(f)
        self.users = { u['id']:u for u in self.config['users']}

    def get_user_roles(self, user_id:str):
        if user_id not in self.users:
            return None
        user_roles = [ur['role'] for ur in self.config['user_roles'] if ur['user'] == user_id]
        if not user_roles:
            return ['reader']
        else:
            return user_roles
    
    def get_user(self, user_id:str):
        return self.users.get(user_id, {})

    def get_user_permissions(self, user_id:str):
        user_roles = self.get_user_roles(user_id)
        if not user_roles:
            return []
        

        permissions = set() 
        for role in user_roles:
            permissions.update([rp['permission'] for rp in self.config['role_permissions'] if rp['role'] == role])

        return list(permissions)  

    def get_status_order(self, status:str):
        return self.status_order.index(status)    
    
    def get_refresher(self):
        return ('config.json', self.load_config)



