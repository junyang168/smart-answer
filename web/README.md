
## Lession Learned

1. React version - hard code to 18.2.0. 
2. Signin with Google and Session Management. This is the most confusing/hacky component I've ever used
    ### Use NextAuth.
    * Identity Provider : src/app/utils/auth.ts
    * Signin callback : src/app/api/auth/[...nextauth]/route.ts
    * User_Profile under component has 2 component, user_profile works for client. it shows user profile ONLY and use useSession(client only). user_profile_with_signin shows either profile or signin button. it's server side
    * in order to use useSession on the client side, change the layout.tsx to wrap all children under <SessionProvider>. However, need to declar a SessionProvider.tsx and export SessionProvider at client side.
    * NextAuth related .env setttings 
    - GOOGLE_CLIENT_ID
    - GOOGLE_CLIENT_SECRET
    - NEXTAUTH_SECRET
    - NEXTAUTH_URL
    - ORG_ID=holylogos
3. Google Sign-in oAuth Configuration
    * [log into Google Cloud console](https://console.cloud.google.com/) with dallas.holy.logos@gmail.com
    * Dev is under Smart-Answer project
    * Production is under Smart Answer Production
4. .env.local at the root dir of project, where package.json is located

5. [How To Fix the “React Hook useEffect Has a Missing Dependency” Error](https://kinsta.com/knowledgebase/react-hook-useeffect-has-a-missing-dependency/)


## Environment
1. Dev
    * Smart-Answer UI: http://localhost:3000 (NextJS)
    * Smart-Answer Service: localhost:60000 (FastAPI)
2. Production
    * Smart-Answer UI Service: http://localhost:3000(NextJS)
    * Smart-Answer Service: localhost:50000 (FastAPI)
    * NgInx Proxy:
        Smart Answer UI: https://smart-answer.servehttp.com => http://localhost:3000
        /fetch-anser => localhost:50000
3. NgInx, SSL, DNS Configuration
    SSL: Let's encrypt
    Proxy: See above
    DNS: NoIP - log in as dallas.holy.logos@gmail.com
    DDUP: https://www.noip.com/support/knowledgebase/installing-the-linux-dynamic-update-client-on-ubuntu

